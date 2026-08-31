"""API 通用依赖与查询辅助。"""

from __future__ import annotations

from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


def get_or_404(
    db: Session,
    model: type[ModelT],
    obj_id: str,
    detail: str = "资源不存在",
) -> ModelT:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=detail)
    return obj


def conflict_from_integrity(exc: IntegrityError, *, table: str, detail: str) -> None:
    """将唯一键冲突翻译为 409，其余 IntegrityError 原样冒泡到 500。

    PostgreSQL 通过 `constraint_name`（uq_*）识别；SQLite 的消息只带
    表名与列名（"UNIQUE constraint failed: <table>.<column>"），按表名兜底。
    """
    orig = getattr(exc, "orig", None)
    constraint = getattr(orig, "constraint_name", None)
    if constraint and "uq_" in str(constraint):
        raise HTTPException(status_code=409, detail=detail) from exc
    if "UNIQUE constraint failed" in str(orig or "") and f"{table}." in str(orig):
        raise HTTPException(status_code=409, detail=detail) from exc
    raise exc
