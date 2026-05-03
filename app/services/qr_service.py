from datetime import datetime, timezone

from redis import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import QRCode
from app.services.image_service import compute_spec_hash, generate_qr_image
from app.services.token_service import generate_qr_token
from app.storage.factory import get_storage

URL_CACHE_TTL = 86400  # 24 h

storage = get_storage()
MAX_RETRIES = 5


def create_qr_code(db: Session, url: str) -> str:
    for _ in range(MAX_RETRIES):
        token = generate_qr_token(url, settings.SERVER_SECRET)
        qr = QRCode(url=url, qr_token=token)
        try:
            db.add(qr)
            db.flush()
            db.commit()
            return token
        except IntegrityError:
            db.rollback()
            continue
    raise RuntimeError("Failed to generate unique token after retries")


def get_qr_code(db: Session, qr_token: str) -> QRCode | None:
    return (
        db.query(QRCode)
        .filter(QRCode.qr_token == qr_token, QRCode.status == "active")
        .first()
    )


def get_qr_code_any_status(db: Session, qr_token: str) -> QRCode | None:
    return db.query(QRCode).filter(QRCode.qr_token == qr_token).first()


def list_qr_codes(db: Session) -> list[QRCode]:
    return (
        db.query(QRCode)
        .order_by(QRCode.created_at.desc())
        .all()
    )


def update_qr_code(db: Session, qr_token: str, url: str, redis: Redis | None = None) -> bool:
    qr = get_qr_code(db, qr_token)
    if not qr:
        return False
    qr.url = url
    qr.updated_at = datetime.now(timezone.utc)
    db.commit()
    if redis:
        redis.delete(f"qr:url:{qr_token}")
    return True


def delete_qr_code(db: Session, qr_token: str, redis: Redis | None = None) -> bool:
    qr = get_qr_code(db, qr_token)
    if not qr:
        return False
    now = datetime.now(timezone.utc)
    qr.status = "deleted"
    qr.deleted_at = now
    qr.updated_at = now
    db.commit()
    if redis:
        redis.delete(f"qr:url:{qr_token}")
    return True


def get_or_generate_image(db: Session, qr_token: str, image_spec: dict) -> str | None:
    qr = get_qr_code(db, qr_token)
    if not qr:
        return None

    spec_hash = compute_spec_hash(image_spec)
    path = f"qr/{qr_token}/{spec_hash}.png"

    if not storage.exists(path):
        image_data = generate_qr_image(qr.url, image_spec)
        storage.save(path, image_data)

    if settings.CDN_BASE_URL:
        return f"{settings.CDN_BASE_URL}/{path}"
    return f"{settings.BASE_URL}/static/{path}"
