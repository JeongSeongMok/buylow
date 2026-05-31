"""`python -m orchestrator.api` → Control API + 대시보드 서버 기동.

보안: 반드시 127.0.0.1에만 바인딩한다(토스 키·매매 제어를 쥐므로 네트워크 노출 금지).
포트는 config(BUYLOW_DASHBOARD_PORT env → config.local.yaml → 기본 8420)에서 읽는다.
시작 시 KRX 크리덴셜이 있으면 환경에 주입해 pykrx 자동 로그인이 되게 한다.
"""

import uvicorn

from ..config import apply_krx_credentials, get_dashboard_port
from .app import create_app


def main() -> None:
    if apply_krx_credentials():
        print("KRX 크리덴셜 적용됨 (pykrx 펀더멘털 조회 가능)")
    else:
        print("KRX 크리덴셜 미설정 — /settings 에서 입력하면 펀더멘털 조회 가능")
    uvicorn.run(create_app(), host="127.0.0.1", port=get_dashboard_port())


if __name__ == "__main__":
    main()
