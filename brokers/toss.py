"""토스증권(Toss) OpenAPI REST 클라이언트.

OAuth2 client-credentials 토큰 발급/캐싱 + 계좌·잔고·매수가능·장운영 조회를 담당한다. 라이브
단계의 주문/체결은 C# 어댑터(adapter/MyTrading.Toss)가 같은 토큰 위에 얹어 처리한다.

KIS(brokers/kis.py)와의 설계 차이(왜 별도 클라이언트인지):
- **인증**: KIS는 appkey/appsecret(JSON) → Toss는 client_id/client_secret(form-urlencoded).
- **계좌 지정**: KIS는 본문에 CANO/ACNT_PRDT_CD → Toss는 `X-Tossinvest-Account` 헤더(accountSeq).
  accountSeq는 getAccounts로 1회 조회해 캐싱한다(계좌번호 시크릿 불필요).
- **모의투자 없음**: Toss는 실전 서버 하나뿐(KIS의 real/demo 분기 없음).
- **응답 봉투**: 대부분 `{"result": ...}` BFF 봉투. 토큰만 OAuth2 표준 형식.
- 금액/수량은 문자열로 오므로 정수/실수로 정규화해 dict로 반환한다(KIS 클라이언트와 동일 도메인).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path

# 스레드 안전 토큰버킷은 KIS 클라이언트와 공유한다(병렬 분봉 적재 시 합산 호출률 제한 — 로직 동일).
from .kis import _TokenBucket

REPO_ROOT = Path(__file__).resolve().parents[1]
# 토큰 캐시(gitignore). client_id별로 분리 저장. BUYLOW_TOSS_TOKEN_CACHE로 옮길 수 있다
# (Docker는 bind-mount된 /app/state로 보내 영속화 — KIS 토큰캐시와 동일 정책).
DEFAULT_TOKEN_CACHE = Path(
    os.environ.get("BUYLOW_TOSS_TOKEN_CACHE") or REPO_ROOT / ".toss_token.json"
)

# 토스증권 OpenAPI 베이스 URL(공식). 실전 단일(모의 서버 없음).
BASE_URL = "https://openapi.tossinvest.com"

_TOKEN_PATH = "/oauth2/token"
_ACCOUNTS_PATH = "/api/v1/accounts"
_HOLDINGS_PATH = "/api/v1/holdings"
_BUYING_POWER_PATH = "/api/v1/buying-power"
_MARKET_CALENDAR_KR_PATH = "/api/v1/market-calendar/KR"
_CANDLES_PATH = "/api/v1/candles"
# 캔들은 호출당 최대 200봉. 1분봉 하루(09:00~15:30 ≈ 390분)는 2~3회 페이지면 충분.
_MAX_CANDLES_PER_CALL = 200
# 토스 요청 한도 초과 코드(HTTP 429). 짧은 백오프 후 재시도한다.
RATE_LIMIT_STATUS = 429


class TossError(RuntimeError):
    """Toss API 오류(인증 실패, HTTP 4xx/5xx 등)."""


class TossClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_cache_path: str | Path | None = None,
        session=None,
        base_url: str = BASE_URL,
        rate_per_sec: float | None = None,
        max_workers: int = 1,
        max_retries: int = 4,
        backoff: float = 0.5,
    ):
        if not client_id or not client_secret:
            raise TossError("Toss client_id/client_secret이 필요합니다 (대시보드 설정에서 입력).")
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self._token_cache_path = Path(token_cache_path or DEFAULT_TOKEN_CACHE)
        self._session = session  # 테스트에서 가짜 세션 주입; 없으면 지연 생성
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._token_lock = threading.Lock()
        self._account_seq: int | None = None
        self._account_no: str | None = None
        self._account_lock = threading.Lock()
        # 레이트리밋: 분봉 병렬 적재 시 공유 토큰버킷으로 합산 호출률을 제한 + 429 시 지수 백오프
        # 재시도(KIS 클라이언트와 동일 정책). rate_per_sec 미지정이면 무제한(단건 조회는 스로틀 불필요).
        self._limiter = _TokenBucket(float(rate_per_sec) if rate_per_sec else 0.0)
        self._max_workers = max(1, int(max_workers))
        self._max_retries = int(max_retries)
        self._backoff = float(backoff)

    def _throttle(self) -> None:
        self._limiter.acquire()

    # ── HTTP 세션 (지연 생성) ────────────────────────────────────────────────
    def _get_session(self):
        if self._session is None:
            import requests  # 지연 임포트(LEAN 런타임 등 requests 없는 환경에서도 모듈 import 가능)
            sess = requests.Session()
            # 병렬 적재 시 동시 요청 수만큼 커넥션 풀을 키워 재사용·경고 회피(KIS와 동일).
            if self._max_workers > 1:
                pool = max(10, self._max_workers)
                adapter = requests.adapters.HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
                sess.mount("https://", adapter)
                sess.mount("http://", adapter)
            self._session = sess
        return self._session

    # ── 토큰 ─────────────────────────────────────────────────────────────────
    def _cache_id(self) -> str:
        return self.client_id[:12]

    def _load_cached_token(self) -> bool:
        try:
            blob = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        rec = blob.get(self._cache_id()) if isinstance(blob, dict) else None
        if not rec:
            return False
        exp = float(rec.get("expires_at", 0))
        if exp > time.time() + 60:  # 만료 1분 전이면 미리 폐기
            self._token, self._token_exp = rec.get("access_token"), exp
            return bool(self._token)
        return False

    def _save_cached_token(self) -> None:
        blob: dict = {}
        try:
            blob = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
            if not isinstance(blob, dict):
                blob = {}
        except (OSError, ValueError):
            blob = {}
        blob[self._cache_id()] = {"access_token": self._token, "expires_at": self._token_exp}
        self._token_cache_path.write_text(json.dumps(blob), encoding="utf-8")
        try:
            self._token_cache_path.chmod(0o600)  # 사용자 전용
        except OSError:
            pass

    def access_token(self) -> str:
        """유효한 접근토큰 반환. 메모리→디스크 캐시→신규 발급 순(이중 검사 잠금).

        ⚠️ Toss는 client당 유효 토큰이 1개이고 재발급 시 직전 토큰이 즉시 무효화된다 — 같은 client_id를
        쓰는 다른 프로세스(예: C# 라이브 어댑터)와 토큰을 두고 경쟁하지 않도록, 디스크 캐시를 공유해
        불필요한 재발급을 줄인다(만료 전까지 재사용).
        """
        if self._token and self._token_exp > time.time() + 60:
            return self._token
        with self._token_lock:
            if self._token and self._token_exp > time.time() + 60:
                return self._token
            if self._load_cached_token():
                return self._token  # type: ignore[return-value]
            return self._issue_token()

    def _issue_token(self) -> str:
        # OAuth2 client-credentials — form-urlencoded(토큰만 BFF 봉투가 아닌 표준 형식).
        self._throttle()
        resp = self._get_session().post(
            f"{self.base_url}{_TOKEN_PATH}",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if resp.status_code != 200:
            raise TossError(f"토큰 발급 실패: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise TossError(f"토큰 응답에 access_token 없음: {data}")
        self._token = token
        # expires_in(초) 기준 만료, 10분 여유.
        self._token_exp = time.time() + float(data.get("expires_in", 86400)) - 600
        self._save_cached_token()
        return token

    # ── HTTP 헬퍼 ──────────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict | None = None, account: bool = False) -> dict:
        """GET 후 BFF 봉투의 result를 반환. account=True면 X-Tossinvest-Account 헤더 부착.

        429(요청 한도 초과)는 지수 백오프 후 재시도한다 — 분봉 병렬 적재 시 한도를 스치면 잠깐 쉬고
        다시 시도해 적재가 중단되지 않게 한다.
        """
        headers = {"authorization": f"Bearer {self.access_token()}"}
        if account:
            headers["X-Tossinvest-Account"] = str(self.account_seq())
        for attempt in range(self._max_retries + 1):
            self._throttle()
            resp = self._get_session().get(
                f"{self.base_url}{path}", headers=headers, params=params or {}, timeout=10
            )
            if resp.status_code == RATE_LIMIT_STATUS and attempt < self._max_retries:
                time.sleep(self._backoff * (2 ** attempt))
                continue
            if resp.status_code != 200:
                raise TossError(f"{path} HTTP {resp.status_code} {resp.text[:200]}")
            data = resp.json()
            return data.get("result", data) if isinstance(data, dict) else data
        raise TossError(f"{path} 요청 한도 재시도 초과(HTTP {RATE_LIMIT_STATUS})")

    @staticmethod
    def _num(v) -> float:
        """문자열 숫자를 float로(콤마/공백/None 방어). 실패 시 0."""
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0

    # ── 계좌 ─────────────────────────────────────────────────────────────────
    def accounts(self) -> list[dict]:
        """계좌 목록(getAccounts). [{accountNo, accountSeq, accountType}]."""
        result = self._get(_ACCOUNTS_PATH)
        return result if isinstance(result, list) else []

    def account_seq(self) -> int:
        """매매/잔고 호출에 쓸 accountSeq. 첫 종합매매(BROKERAGE) 계좌를 1회 조회해 캐싱.

        Toss의 모든 계좌 컨텍스트 API는 이 값을 X-Tossinvest-Account 헤더로 받는다(KIS의 계좌번호 대용).
        """
        if self._account_seq is not None:
            return self._account_seq
        with self._account_lock:
            if self._account_seq is not None:
                return self._account_seq
            accs = self.accounts()
            if not accs:
                raise TossError("토스 계좌가 없습니다(getAccounts 빈 응답) — 계좌 개설/권한을 확인하세요.")
            # 종합매매 우선, 없으면 첫 계좌.
            acc = next((a for a in accs if a.get("accountType") == "BROKERAGE"), accs[0])
            self._account_seq = int(acc["accountSeq"])
            self._account_no = str(acc.get("accountNo") or "")
            return self._account_seq

    def account_no(self) -> str:
        """표시용 계좌번호(마스킹은 호출부). account_seq 해석 시 함께 캐싱된다."""
        self.account_seq()
        return self._account_no or ""

    # ── 잔고/매수가능 ──────────────────────────────────────────────────────────
    def fetch_buying_power(self, currency: str = "KRW") -> int:
        """매수 가능 금액(현금 기준, getBuyingPower). KRW 정수."""
        result = self._get(_BUYING_POWER_PATH, {"currency": currency}, account=True)
        return int(round(self._num(result.get("cashBuyingPower"))))

    def fetch_balance(self) -> dict:
        """보유주식(getHoldings) + 매수가능(getBuyingPower)을 KIS와 같은 형태로 정규화.

        반환: {holdings: [{ticker,name,qty,avg_price,cur_price,eval_amount,pnl,pnl_pct}],
               deposit, d2_deposit, total_eval, net_asset, buying_power}. 국내(KR) 주식만 포함.
        ⚠️ Toss는 KRW/USD를 함께 주며, 여기선 KRX 매매용이라 marketCountry=='KR'만 취한다.
        금액은 종목 단위는 종목 통화 그대로(KR=KRW), 합산 요약은 KRW(.krw) 필드를 쓴다.
        """
        result = self._get(_HOLDINGS_PATH, account=True)
        holdings = []
        for item in (result.get("items") or []):
            if item.get("marketCountry") != "KR":
                continue  # 국내주식만(해외는 별도 통화·시장)
            qty = int(self._num(item.get("quantity")))
            if qty <= 0:
                continue
            mv = item.get("marketValue") or {}
            pl = item.get("profitLoss") or {}
            holdings.append({
                "ticker": item.get("symbol", ""),
                "name": item.get("name", ""),
                "qty": qty,
                "avg_price": int(round(self._num(item.get("averagePurchasePrice")))),
                "cur_price": int(round(self._num(item.get("lastPrice")))),
                "eval_amount": int(round(self._num(mv.get("amount")))),
                "pnl": int(round(self._num(pl.get("amount")))),
                "pnl_pct": self._num(pl.get("rate")) * 100.0,  # 0.1077 → 10.77%
            })
        # 합산 요약(KRW). 매수가능은 별도 호출.
        ov_eval = ((result.get("marketValue") or {}).get("amount") or {})
        buying_power = self.fetch_buying_power("KRW")
        return {
            "holdings": holdings,
            # Toss는 '예수금'을 따로 주지 않으므로 매수가능금액을 예수금으로도 표시(현금 기준 동일 의미).
            "deposit": buying_power,
            "d2_deposit": 0,
            "total_eval": int(round(self._num(ov_eval.get("krw")))),
            # 순자산 ≈ 평가금액 + 현금. Toss가 직접 주지 않아 근사(표시용).
            "net_asset": int(round(self._num(ov_eval.get("krw")))) + buying_power,
            "buying_power": buying_power,
        }

    # ── 분봉 (백테스트 데이터) ───────────────────────────────────────────────────
    def fetch_minute(self, ticker: str, day: date,
                     open_hhmmss: str = "090000", close_hhmmss: str = "153000") -> list[dict]:
        """하루치 1분봉을 정규화 dict 리스트(시간 오름차순)로. getCandles(interval=1m)를 페이지네이션.

        반환 dict: {ms: 자정(KST) 기준 밀리초, time: 'HHMMSS', open/high/low/close: int(원), volume: int}
        — KIS fetch_minute와 동일 형태라 etl.kis_minute.ingest_minute가 그대로 적재한다.

        ⚠️ 토스 캔들은 호출당 ≤200봉, 최신부터 과거로 `before`(exclusive) 커서로 페이지네이션한다.
        토스 보관 기간 밖이거나 데이터 없으면 빈 리스트. (계좌 불필요 — 토큰만으로 조회)
        """
        def _ms(hhmmss: str) -> int:
            return (int(hhmmss[:2]) * 3600 + int(hhmmss[2:4]) * 60 + int(hhmmss[4:6])) * 1000

        open_ms, close_ms = _ms(open_hhmmss), _ms(close_hhmmss)
        by_ms: dict[int, dict] = {}
        # before는 '이 시각 이전' 봉만 반환(exclusive) → 마감봉을 포함하려면 마감초+1초로 둔다.
        before = f"{day.isoformat()}T{close_hhmmss[:2]}:{close_hhmmss[2:4]}:59+09:00"
        for _ in range(8):  # 하루 ~390분/200 = 2회면 충분, 무한루프 방지 상한
            result = self._get(_CANDLES_PATH, {
                "symbol": ticker, "interval": "1m", "count": _MAX_CANDLES_PER_CALL,
                "before": before, "adjusted": "true",
            })
            candles = result.get("candles") or []
            if not candles:
                break
            new = False
            earliest_ms: int | None = None
            passed_day = False
            for c in candles:
                ts = c.get("timestamp")
                if not ts:
                    continue
                dt = datetime.fromisoformat(ts)  # '2026-03-25T09:00:00+09:00' (KST)
                if dt.date() != day:
                    passed_day = True  # 이전 날짜로 넘어감 → 이 종목/날짜는 끝
                    continue
                ms = (dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000
                if earliest_ms is None or ms < earliest_ms:
                    earliest_ms = ms
                clpr = c.get("closePrice")
                if clpr is None or not (open_ms <= ms <= close_ms) or ms in by_ms:
                    continue
                by_ms[ms] = {
                    "ms": ms,
                    "time": f"{dt.hour:02d}{dt.minute:02d}{dt.second:02d}",
                    "open": int(float(c["openPrice"])),
                    "high": int(float(c["highPrice"])),
                    "low": int(float(c["lowPrice"])),
                    "close": int(float(clpr)),
                    "volume": int(float(c.get("volume", 0))),
                }
                new = True
            next_before = result.get("nextBefore")
            # 개장 이전까지 받았거나, 더 받을 페이지가 없거나, 새 봉이 없으면 종료.
            if (passed_day or not next_before or not new
                    or (earliest_ms is not None and earliest_ms <= open_ms)):
                break
            before = next_before
        return [by_ms[k] for k in sorted(by_ms)]

    # ── 장 운영 ─────────────────────────────────────────────────────────────────
    def check_market_open(self, day: date) -> bool:
        """해당일이 국내 개장(거래)일이면 True (getKrMarketCalendar).

        달력은 기준일의 전일/당일/익일 '영업일' 정보를 준다. 기준일=day로 조회해 today.date가 day와
        같고 정규장 정보가 있으면 거래일로 본다(주말/공휴일은 today.date가 day와 달라진다).
        """
        result = self._get(_MARKET_CALENDAR_KR_PATH, {"date": day.isoformat()})
        today = result.get("today") or {}
        if today.get("date") != day.isoformat():
            return False
        integrated = today.get("integrated") or {}
        return bool(integrated.get("regularMarket"))


def from_config(**overrides):
    """config의 Toss 자격증명으로 TossClient를 생성한다(없으면 TossError)."""
    from orchestrator import config
    cred = config.get_toss_credentials()
    return TossClient(cred["client_id"], cred["client_secret"], **overrides)
