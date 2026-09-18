from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = {"schema": "warehouse"}
