"""`python -m orchestrator.api` → Control API + 대시보드 서버 기동.

보안: 기본은 127.0.0.1에만 바인딩한다(토스 키·매매 제어를 쥐므로 네트워크 노출 금지).
Docker에서만 BUYLOW_DASHBOARD_HOST=0.0.0.0으로 풀고, docker-compose가 호스트 쪽을
127.0.0.1로 묶어 외부 노출은 막는다(docs/DEVELOPMENT.md). 호스트/포트는 config에서 읽는다
(BUYLOW_DASHBOARD_HOST/PORT env → config.local.yaml → 기본 127.0.0.1:8420).
시작 시 KRX 크리덴셜 주입 + (설정 시) 일일 증분 적재 스케줄러 기동.
"""

import uvicorn

from ..config import (
    apply_krx_credentials,
    get_dashboard_host,
    get_dashboard_port,
    get_scheduler_config,
)
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
        cfg = get_scheduler_config()
        extra = f", 분봉 {len(cfg['minute_universe'])}종목 포함" if cfg["minute_universe"] else ""
        print(f"자동 증분 적재 스케줄러 가동 (매 {cfg['interval_minutes']}분{extra})")

    uvicorn.run(create_app(jobs=jobs), host=get_dashboard_host(), port=get_dashboard_port())


if __name__ == "__main__":
    main()
