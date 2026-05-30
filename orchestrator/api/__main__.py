"""`python -m orchestrator.api` → Control API + 대시보드 서버 기동.

보안: 반드시 127.0.0.1에만 바인딩한다(토스 키·매매 제어를 쥐므로 네트워크 노출 금지).
포트는 BUYLOW_DASHBOARD_PORT 환경변수로 변경 가능(기본 8420).
"""

import os

import uvicorn

from .app import create_app

DEFAULT_PORT = 8420


def main() -> None:
    port = int(os.environ.get("BUYLOW_DASHBOARD_PORT", DEFAULT_PORT))
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
