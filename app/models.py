import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class QRCode(Base):
    __tablename__ = "qr_codes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    qr_token = Column(String(10), nullable=False, unique=True, index=True)
    url = Column(String(2048), nullable=False)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    click_count = Column(Integer, nullable=False, default=0)
    last_clicked_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)


class QRClickStat(Base):
    __tablename__ = "qr_click_stats"

    qr_token = Column(String(10), primary_key=True)
    hour_bucket = Column(DateTime(timezone=True), primary_key=True)
    click_count = Column(Integer, nullable=False, default=0)
