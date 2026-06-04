"""한국투자증권(KIS) OpenAPI REST 클라이언트.

OAuth 접근토큰 발급/캐싱 + 국내주식 시세 조회를 담당한다. 라이브 단계의 주문/실시간(웹소켓)도
이 토큰 위에 얹을 예정이라, 데이터 계층과 분리해 재사용 가능한 클라이언트로 둔다.

설계상 주의점(왜 이렇게 짰는지):
- **토큰 디스크 캐싱**: KIS 접근토큰은 24h 유효이고 짧은 간격 재발급을 서버가 차단한다.
  매 호출/프로세스마다 재발급하면 곧 막히므로, 토큰을 디스크에 캐싱해 만료 전까지 재사용한다.
- **requests 지연 임포트**: LEAN 런타임 등 requests 없는 환경에서도 모듈 임포트는 되게 한다.
- **시크릿은 주입**: 키는 repo에 없고(config.local.yaml, gitignore) 호출부가 넘긴다.
- 가격은 KRX 원화 실제값(정수)로 정규화해 dict로 반환한다. LEAN 스케일링은 etl.lean_format 몫.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# 토큰 캐시(gitignore). 키별·환경별로 분리 저장한다.
DEFAULT_TOKEN_CACHE = REPO_ROOT / ".kis_token.json"

# 실전/모의 REST 베이스 URL (KIS 공식). 라이브는 '실전' 목표.
BASE_URLS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "demo": "https://openapivts.koreainvestment.com:29443",
}

# 국내주식 기간별시세(일/주/월/년) — 수정주가 지원, 호출당 최대 100건.
_DAILY_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_DAILY_CHART_TR = "FHKST03010100"  # 실전·모의 동일
_MAX_ROWS_PER_CALL = 100
# 100건(거래일) ≈ 달력 140일 정도. 윈도를 넉넉히 잡고 중복은 날짜로 제거한다.
_WINDOW_DAYS = 130

# 주식일별분봉조회 — 호출당 최대 120건(분), 과거 분봉은 당사 보관분(최대 약 1년)만.
_MINUTE_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_MINUTE_CHART_TR = "FHKST03010230"
_MAX_MIN_ROWS_PER_CALL = 120

# 주식잔고조회 — 보유종목(output1) + 예수금/평가(output2). 실전/모의 TR 분기.
_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_BALANCE_TR = {"real": "TTTC8434R", "demo": "VTTC8434R"}
# 국내휴장일조회 — 개장(거래)일 여부. 실전/모의 공통 TR. (일 1회 호출 권장)
_HOLIDAY_PATH = "/uapi/domestic-stock/v1/quotations/chk-holiday"
_HOLIDAY_TR = "CTCA0903R"


# KIS 레이트리밋 에러코드 — 초당 거래건수 초과. 분봉처럼 호출을 연발하면 즉시 걸린다.
RATE_LIMIT_CODE = "EGW00201"

# KIS 실전 REST 유량 한도는 대략 초당 ~20건(앱키 단위로 서버에서 합산). 마진을 두고 보수적으로.
DEFAULT_RATE_PER_SEC = 8.0


class _TokenBucket:
    """스레드 안전 토큰버킷 — 여러 스레드가 공유해도 '합산' 호출률이 rate(초당)를 넘지 않게 한다.

    왜 필요한가: 분봉 적재를 병렬화하면 네트워크 왕복 지연은 가릴 수 있지만, KIS 유량 제한은
    동시성이 아니라 '초당 호출 수'에 걸린다. 동시 요청을 띄우되 토큰을 받은 호출만 내보내
    버스트가 한도를 넘지 않게 매끄럽게 흘려보낸다. (직전 호출 시각만 보던 비-스레드세이프
    스로틀을 대체.)

    clock/sleep 주입은 결정론적 단위 테스트용(실제 시간에 의존하지 않게).
    """

    def __init__(self, rate: float, burst: float | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        self.rate = float(rate)
        # 버스트 용량: 기본 = 초당 rate만큼(최소 1). 토큰이 차 있으면 그만큼 즉시 통과.
        self.capacity = float(burst if burst is not None else max(1.0, self.rate))
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.rate <= 0:  # 0/음수 = 무제한(테스트·옵트아웃)
            return
        while True:
            with self._lock:
                now = self._clock()
                # 경과 시간만큼 토큰 보충(누수형). 잠금 안에서만 토큰을 만지므로 합산률이 보장된다.
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            self._sleep(wait)  # 잠금 밖에서 대기 — 다른 스레드의 토큰 계산을 막지 않는다.


class KisError(RuntimeError):
    """KIS API 오류(인증 실패, rt_cd != 0 등)."""


class KisClient:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        env: str = "real",
        token_cache_path: str | Path | None = None,
        session=None,
        min_interval: float = 0.12,
        rate_per_sec: float | None = None,
        max_workers: int = 1,
        max_retries: int = 4,
        backoff: float = 0.5,
    ):
        if not app_key or not app_secret:
            raise KisError("KIS app_key/app_secret이 필요합니다 (대시보드 설정에서 입력).")
        if env not in BASE_URLS:
            raise KisError(f"알 수 없는 env: {env} (가능: {list(BASE_URLS)})")
        self.app_key = app_key
        self.app_secret = app_secret
        self.env = env
        self.base_url = BASE_URLS[env]
        self._token_cache_path = Path(token_cache_path or DEFAULT_TOKEN_CACHE)
        self._session = session  # 테스트에서 가짜 세션 주입; 없으면 지연 생성
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._token_lock = threading.Lock()  # 병렬 첫 호출 시 토큰 중복 발급 방지
        # 레이트리밋 대응: 공유 토큰버킷으로 합산 호출률을 제한 + EGW00201 시 지수 백오프 재시도.
        # rate_per_sec 우선; 없으면 옛 min_interval에서 환산(0이면 무제한). 병렬 적재가 안전하게
        # 한도 가까이 호출률을 끌어올리되 넘지 않게 한다 — 자세한 이유는 _TokenBucket 참고.
        self._min_interval = float(min_interval)
        if rate_per_sec is not None:
            rate = float(rate_per_sec)
        elif self._min_interval > 0:
            rate = 1.0 / self._min_interval
        else:
            rate = 0.0  # 무제한(테스트)
        self._limiter = _TokenBucket(rate)
        # HTTP 커넥션 풀 크기 힌트 — 동시 요청 수만큼 풀이 있어야 "pool is full" 경고를 피한다.
        self._max_workers = max(1, int(max_workers))
        self._max_retries = int(max_retries)
        self._backoff = float(backoff)

    def _throttle(self) -> None:
        """공유 토큰버킷에서 토큰 1개를 받을 때까지 대기 — 스레드 안전한 초당 호출 상한."""
        self._limiter.acquire()

    # ── HTTP 세션 (지연 생성) ────────────────────────────────────────────────
    def _get_session(self):
        if self._session is None:
            import requests  # 지연 임포트
            sess = requests.Session()
            # 병렬 적재 시 동시 요청 수만큼 커넥션 풀을 키워 재사용·경고 회피.
            if self._max_workers > 1:
                pool = max(10, self._max_workers)
                adapter = requests.adapters.HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
                sess.mount("https://", adapter)
                sess.mount("http://", adapter)
            self._session = sess
        return self._session

    # ── 토큰 ─────────────────────────────────────────────────────────────────
    def _cache_id(self) -> str:
        # 같은 캐시 파일을 여러 키/환경이 공유해도 섞이지 않게 식별자로 구분.
        return f"{self.env}:{self.app_key[:8]}"

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
        # 토큰 파일은 사용자 전용 권한으로(0600) 저장 — 다른 사용자 노출 방지.
        self._token_cache_path.write_text(json.dumps(blob), encoding="utf-8")
        try:
            self._token_cache_path.chmod(0o600)
        except OSError:
            pass

    def access_token(self) -> str:
        """유효한 접근토큰 반환. 메모리→디스크 캐시→신규 발급 순.

        병렬 호출 대비: 메모리 캐시는 잠금 없이(핫패스) 보고, 발급은 잠금으로 직렬화해
        여러 스레드가 동시에 토큰을 중복 발급(서버가 차단)하지 않게 한다(이중 검사).
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
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        self._throttle()
        resp = self._get_session().post(
            url, data=json.dumps(body),
            headers={"content-type": "application/json"}, timeout=10,
        )
        if resp.status_code != 200:
            raise KisError(f"토큰 발급 실패: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise KisError(f"토큰 응답에 access_token 없음: {data}")
        # expires_in(초) 기준으로 만료시각 계산, 10분 여유.
        self._token = token
        self._token_exp = time.time() + float(data.get("expires_in", 86400)) - 600
        self._save_cached_token()
        return token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }

    # ── 시세 조회 ──────────────────────────────────────────────────────────────
    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        # KIS는 레이트리밋 초과 시 HTTP 500 + 본문 msg_cd=EGW00201을 준다 → 백오프 후 재시도.
        for attempt in range(self._max_retries + 1):
            self._throttle()
            resp = self._get_session().get(
                f"{self.base_url}{path}", headers=self._headers(tr_id), params=params, timeout=10,
            )
            try:
                data = resp.json()
            except ValueError:
                data = None
            if resp.status_code == 200 and data is not None and str(data.get("rt_cd", "0")) == "0":
                return data
            # 레이트리밋이면 지수 백오프 후 재시도(상태코드 무관 — 본문 코드로 판단).
            if data and data.get("msg_cd") == RATE_LIMIT_CODE and attempt < self._max_retries:
                time.sleep(self._backoff * (2 ** attempt))
                continue
            if resp.status_code != 200:
                raise KisError(f"{path} HTTP {resp.status_code} {resp.text[:200]}")
            raise KisError(f"{path} rt_cd={data.get('rt_cd')} {data.get('msg1')}")
        raise KisError(f"{path} 레이트리밋 재시도 초과({RATE_LIMIT_CODE})")

    def _fetch_daily_window(self, ticker: str, start: date, end: date,
                            adjusted: bool) -> list[dict]:
        """단일 호출(≤100건) — output2 일봉 배열을 정규화 dict 리스트로."""
        data = self._get(_DAILY_CHART_PATH, _DAILY_CHART_TR, {
            "FID_COND_MRKT_DIV_CODE": "J",  # KRX 주식
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",     # 일봉
            "FID_ORG_ADJ_PRC": "0" if adjusted else "1",  # 0:수정주가
        })
        out = []
        for row in (data.get("output2") or []):
            d = row.get("stck_bsop_date")
            clpr = row.get("stck_clpr")
            if not d or not clpr:  # 빈 행(미래일자 등) 스킵
                continue
            out.append({
                "day": date(int(d[:4]), int(d[4:6]), int(d[6:8])),
                "open": int(float(row["stck_oprc"])),
                "high": int(float(row["stck_hgpr"])),
                "low": int(float(row["stck_lwpr"])),
                "close": int(float(clpr)),
                "volume": int(float(row.get("acml_vol", 0))),
            })
        return out

    def fetch_daily(self, ticker: str, start: date, end: date,
                    adjusted: bool = True) -> list[dict]:
        """[start, end] 일봉을 정규화 dict 리스트로(오름차순). 100건 제한을 윈도로 분할 조회.

        반환 dict: {day: date, open, high, low, close: int(원), volume: int}.
        """
        by_day: dict[date, dict] = {}
        win_end = end
        while win_end >= start:
            win_start = max(start, win_end - timedelta(days=_WINDOW_DAYS))
            rows = self._fetch_daily_window(ticker, win_start, win_end, adjusted)
            for r in rows:
                if start <= r["day"] <= end:
                    by_day[r["day"]] = r
            # 한 윈도가 100건(거래일)을 넘겨 잘렸을 수 있다 → 다음 윈도를 더 좁게.
            if len(rows) >= _MAX_ROWS_PER_CALL and rows:
                win_end = min(r["day"] for r in rows) - timedelta(days=1)
            else:
                win_end = win_start - timedelta(days=1)
        return [by_day[d] for d in sorted(by_day)]

    def fetch_today(self, ticker: str, today: date, adjusted: bool = True) -> dict | None:
        """당일 봉 1개(장중이면 진행 중 OHLC). pykrx가 아직 못 주는 '오늘'을 메우는 용도."""
        rows = self._fetch_daily_window(ticker, today, today, adjusted)
        for r in rows:
            if r["day"] == today:
                return r
        return None

    # ── 분봉 ──────────────────────────────────────────────────────────────────
    def _fetch_minute_at(self, ticker: str, day: date, hhmmss: str) -> list[dict]:
        """단일 호출(≤120건) — hhmmss 시각에서 과거 방향으로 분봉을 받아 정규화 dict로.

        반환 dict: {ms: 자정기준 밀리초, time: 'HHMMSS', open/high/low/close: int(원), volume: int}.
        """
        data = self._get(_MINUTE_CHART_PATH, _MINUTE_CHART_TR, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": hhmmss,
            "FID_INPUT_DATE_1": day.strftime("%Y%m%d"),
            "FID_PW_DATA_INCU_YN": "Y",   # 과거(보관분) 분봉 포함
            "FID_FAKE_TICK_INCU_YN": "",  # 허봉 제외
        })
        out = []
        for row in (data.get("output2") or []):
            t = row.get("stck_cntg_hour")
            clpr = row.get("stck_prpr")  # 분봉의 현재가 = 그 분의 종가
            if not t or not clpr:
                continue
            t = t.zfill(6)
            ms = (int(t[:2]) * 3600 + int(t[2:4]) * 60 + int(t[4:6])) * 1000
            out.append({
                "ms": ms,
                "time": t,
                "open": int(float(row["stck_oprc"])),
                "high": int(float(row["stck_hgpr"])),
                "low": int(float(row["stck_lwpr"])),
                "close": int(float(clpr)),
                "volume": int(float(row.get("cntg_vol", 0))),
            })
        return out

    def fetch_minute(self, ticker: str, day: date,
                     open_hhmmss: str = "090000", close_hhmmss: str = "153000") -> list[dict]:
        """하루치 분봉을 정규화 dict 리스트(시간 오름차순)로. 120건 제한을 시각 역방향으로 분할 호출.

        ⚠️ KIS는 과거 분봉을 보관분(최대 약 1년)만 제공한다 — 그 이전은 빈 결과.
        """
        open_ms = (int(open_hhmmss[:2]) * 3600 + int(open_hhmmss[2:4]) * 60) * 1000
        by_ms: dict[int, dict] = {}
        cursor = close_hhmmss
        # 정규장 분 수(약 390)보다 넉넉한 호출 상한(무한루프 방지).
        for _ in range(_MAX_MIN_ROWS_PER_CALL):  # 120회면 분봉 한도 충분
            rows = self._fetch_minute_at(ticker, day, cursor)
            rows = [r for r in rows if r["ms"] >= open_ms]
            if not rows:
                break
            new = False
            for r in rows:
                if r["ms"] not in by_ms:
                    by_ms[r["ms"]] = r
                    new = True
            earliest = min(r["ms"] for r in rows)
            if earliest <= open_ms or not new:
                break
            # 다음 호출은 가장 이른 분 1분 전부터 과거로.
            prev = earliest // 1000 - 60
            if prev < open_ms // 1000:
                break
            cursor = f"{prev // 3600:02d}{(prev % 3600) // 60:02d}00"
        return [by_ms[k] for k in sorted(by_ms)]


    # ── 주문/계좌 조회 (매매 탭 읽기용) ────────────────────────────────────────
    @staticmethod
    def _num(v) -> float:
        """KIS 문자열 숫자를 float로(콤마/공백/빈값 방어). 실패 시 0."""
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0

    def fetch_balance(self, cano: str, acnt_prdt_cd: str = "01") -> dict:
        """주식잔고조회 — 보유종목 + 예수금/평가. env(real/demo)에 맞는 TR로 조회.

        반환: {holdings: [{ticker,name,qty,avg_price,cur_price,eval_amount,pnl,pnl_pct}],
               deposit, d2_deposit, total_eval, net_asset}. 금액은 KRW 정수(반올림).
        ⚠️ 첫 페이지만 읽는다(보통 보유종목 수가 적음). 다수 보유 시 페이지네이션은 후속.
        """
        if not cano:
            raise KisError("계좌번호(CANO)가 필요합니다 (설정에서 KIS 계좌번호 입력).")
        tr = _BALANCE_TR.get(self.env, _BALANCE_TR["real"])
        data = self._get(_BALANCE_PATH, tr, {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        })
        holdings = []
        for row in (data.get("output1") or []):
            qty = int(self._num(row.get("hldg_qty")))
            if qty <= 0:
                continue
            avg = self._num(row.get("pchs_avg_pric"))
            cur = self._num(row.get("prpr"))
            pnl = self._num(row.get("evlu_pfls_amt"))
            holdings.append({
                "ticker": row.get("pdno", ""),
                "name": row.get("prdt_name", ""),
                "qty": qty,
                "avg_price": int(round(avg)),
                "cur_price": int(round(cur)),
                "eval_amount": int(round(self._num(row.get("evlu_amt")))),
                "pnl": int(round(pnl)),
                "pnl_pct": self._num(row.get("evlu_pfls_rt")),
            })
        out2 = (data.get("output2") or [{}])
        o2 = out2[0] if out2 else {}
        return {
            "holdings": holdings,
            "deposit": int(round(self._num(o2.get("dnca_tot_amt")))),
            "d2_deposit": int(round(self._num(o2.get("prvs_rcdl_excc_amt")))),
            "total_eval": int(round(self._num(o2.get("tot_evlu_amt")))),
            "net_asset": int(round(self._num(o2.get("nass_amt")))),
        }

    def check_market_open(self, day: date) -> bool:
        """국내휴장일조회 — 해당일이 개장(거래)일이면 True. (env 무관 공통 TR)"""
        data = self._get(_HOLIDAY_PATH, _HOLIDAY_TR, {
            "BASS_DT": day.strftime("%Y%m%d"),
            "CTX_AREA_NK": "",
            "CTX_AREA_FK": "",
        })
        ymd = day.strftime("%Y%m%d")
        for row in (data.get("output") or []):
            if row.get("bass_dt") == ymd:
                return row.get("opnd_yn") == "Y"
        return False


def from_config(broker: str | None = None, **overrides):
    """config의 KIS 자격증명으로 **데이터(시세·분봉)용** 클라이언트 생성.

    ★ 데이터는 계좌가 필요 없어 **항상 실전 도메인(env=real)**에서 받는다 — 모의투자(kis_demo)를
    골라도 분봉/시세는 실전 서버에서 받는다(모의 서버는 분봉 등 일부 시세 API가 제한될 수 있으므로).
    단 사용하는 **앱키는 선택한 증권사 것**을 쓴다(모의 증권사면 모의 앱키 → 실전 도메인 시세 조회).

    overrides로 rate_per_sec·max_workers 등을 넘겨 병렬 적재용 클라이언트를 만들 수 있다.
    """
    from orchestrator import config
    cred = config.get_kis_credentials(broker)
    return KisClient(cred["app_key"], cred["app_secret"], env="real", **overrides)
