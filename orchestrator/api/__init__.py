"""Control API (FastAPI). 대시보드/CLI가 호출하는 단일 계약."""

from .app import create_app

__all__ = ["create_app"]
