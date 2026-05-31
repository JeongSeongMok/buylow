"""`python -m orchestrator.api` → Control API + 대시보드 서버 기동.

보안: 반드시 127.0.0.1에만 바인딩한다(토스 키·매매 제어를 쥐므로 네트워크 노출 금지).
포트는 config(BUYLOW_DASHBOARD_PORT env → config.local.yaml → 기본 8420)에서 읽는다.
시작 시 KRX 크리덴셜 주입 + (설정 시) 일일 증분 적재 스케줄러 기동.
"""

import uvicorn

from ..config import apply_krx_credentials, get_dashboard_port
from ..jobs import JobManager
from ..scheduler import start_scheduler
from .app import create_app


def main() -> None:
    if apply_krx_credentials():
        print("KRX 크리덴셜 적용됨 (펀더멘털/수급 조회 가능)")
    else:
        print("KRX 크리덴셜 미설정 — /settings 에서 입력 가능")

    jobs = JobManager()
    if start_scheduler(jobs) is not None:
        print("일일 증분 적재 스케줄러 가동")

    uvicorn.run(create_app(jobs=jobs), host="127.0.0.1", port=get_dashboard_port())


if __name__ == "__main__":
    main()
