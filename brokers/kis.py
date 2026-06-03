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

    # ── HTTP 세션 (지연 생성) ────────────────────────────────────────────────
    def _get_session(self):
        if self._session is None:
            import requests  # 지연 임포트
            self._session = requests.Session()
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
        """유효한 접근토큰 반환. 메모리→디스크 캐시→신규 발급 순."""
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
        resp = self._get_session().get(
            f"{self.base_url}{path}", headers=self._headers(tr_id), params=params, timeout=10,
        )
        if resp.status_code != 200:
            raise KisError(f"{path} HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        if str(data.get("rt_cd", "0")) != "0":
            raise KisError(f"{path} rt_cd={data.get('rt_cd')} {data.get('msg1')}")
        return data

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


def from_config():
    """config.local.yaml/env의 KIS 자격증명으로 클라이언트 생성. 라이브 목표는 실전(real)."""
    from orchestrator import config
    cred = config.get_kis_credentials()
    return KisClient(cred["app_key"], cred["app_secret"], env="real")
