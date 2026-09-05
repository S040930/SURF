"""FastAPI 应用入口。

使用应用工厂模式创建 FastAPI 实例,模块级 `app` 便于 uvicorn 直接加载。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.experiments import router as experiments_router
from app.api.health import router as health_router
from app.api.r20_archive import router as r20_archive_router
from app.api.r21_platform import router as r21_platform_router
from app.api.r22_platform import router as r22_platform_router
from app.api.r23_platform import router as r23_platform_router
from app.core.config import settings
from app.db.session import engine as _engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Release the database pool on shutdown."""
    try:
        yield
    finally:
        _engine.dispose()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title="记忆增强批改实验系统",
        description="SURF-2026-0031 实验后端",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(experiments_router, prefix="/api/experiments", tags=["experiments"])
    app.include_router(r20_archive_router, prefix="/api/r20", tags=["r20-archive"])
    app.include_router(r21_platform_router, prefix="/api/r21", tags=["r21-platform"])
    app.include_router(r22_platform_router, prefix="/api/r22", tags=["r22-platform"])
    app.include_router(r23_platform_router, prefix="/api/r23", tags=["r23-platform"])

    return app


app = create_app()
