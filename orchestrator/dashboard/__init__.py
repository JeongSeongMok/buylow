"""로컬 브라우저 대시보드 (HTMX + Jinja).

Control API와 같은 FastAPI 앱에 HTML 라우트를 얹는다. 비즈니스 로직은 API/Runner/Store에 있고,
여기서는 표면(화면)만 담당한다(docs/ARCHITECTURE.md). 차트 등 고도화는 이후 단계.
"""

from .routes import register_dashboard

__all__ = ["register_dashboard"]
