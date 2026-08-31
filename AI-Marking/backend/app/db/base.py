"""SQLAlchemy 2.0 声明式基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""

    pass


# 导入模型确保元数据注册（避免循环导入，必须放在 Base 定义之后）。
from app.models import r20, r21, r22, r23  # noqa: E402, F401
