from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import QRCode
from app.services.image_service import compute_spec_hash, generate_qr_image
from app.services.token_service import generate_qr_token
from app.storage.factory import get_storage

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


def update_qr_code(db: Session, qr_token: str, url: str) -> bool:
    qr = get_qr_code(db, qr_token)
    if not qr:
        return False
    qr.url = url
    qr.updated_at = datetime.now(timezone.utc)
    db.commit()
    return True


def delete_qr_code(db: Session, qr_token: str) -> bool:
    qr = get_qr_code(db, qr_token)
    if not qr:
        return False
    now = datetime.now(timezone.utc)
    qr.status = "deleted"
    qr.deleted_at = now
    qr.updated_at = now
    db.commit()
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


def redirect_qr_code(db: Session, qr_token: str) -> str | None:
    qr = get_qr_code(db, qr_token)
    if not qr:
        return None
    qr.last_clicked_at = datetime.now(timezone.utc)
    db.commit()
    return qr.url
