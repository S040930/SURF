"""健康检查路由。"""

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthOut(BaseModel):
    status: str
    timestamp: str


@router.get("/health", response_model=HealthOut)
def health_check() -> HealthOut:
    """返回服务健康状态与当前 UTC 时间戳。"""
    return HealthOut(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
