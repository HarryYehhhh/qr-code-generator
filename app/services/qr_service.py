import hashlib
from datetime import datetime, timezone

from opentelemetry import trace
from redis import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import QRCode
from app.services.image_service import (
    cache_lookup,
    compute_spec_hash,
    generate_qr_image,
)
from app.services.token_service import generate_qr_token

URL_CACHE_TTL = 86400  # 24 h
IMG_CACHE_TTL = 7 * 86400  # 7 d

MAX_RETRIES = 5

_tracer = trace.get_tracer(__name__)


def create_qr_code(db: Session, url: str, redis: Redis | None = None) -> str:
    with _tracer.start_as_current_span("qr_service.create") as span:
        for _ in range(MAX_RETRIES):
            token = generate_qr_token(url, settings.SERVER_SECRET)
            qr = QRCode(url=url, qr_token=token)
            try:
                db.add(qr)
                db.flush()
                db.commit()
                if redis:
                    redis.setex(f"qr:url:{token}", URL_CACHE_TTL, url)
                span.set_attribute("qr.token", token)
                return token
            except IntegrityError:
                db.rollback()
                continue
        raise RuntimeError("Failed to generate unique token after retries")


def get_qr_code(db: Session, qr_token: str) -> QRCode | None:
    with _tracer.start_as_current_span("qr_service.get") as span:
        span.set_attribute("qr.token", qr_token)
        return (
            db.query(QRCode)
            .filter(QRCode.qr_token == qr_token, QRCode.status == "active")
            .first()
        )


def get_qr_code_any_status(db: Session, qr_token: str) -> QRCode | None:
    return db.query(QRCode).filter(QRCode.qr_token == qr_token).first()


def list_qr_codes(db: Session) -> list[QRCode]:
    with _tracer.start_as_current_span("qr_service.list"):
        return (
            db.query(QRCode)
            .order_by(QRCode.created_at.desc())
            .all()
        )


def update_qr_code(db: Session, qr_token: str, url: str, redis: Redis | None = None) -> bool:
    with _tracer.start_as_current_span("qr_service.update") as span:
        span.set_attribute("qr.token", qr_token)
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
    with _tracer.start_as_current_span("qr_service.delete") as span:
        span.set_attribute("qr.token", qr_token)
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


def _img_cache_key(url: str, image_spec: dict) -> str:
    spec_hash = compute_spec_hash(image_spec)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    return f"qr:img:{spec_hash}:{url_hash}"


def get_or_generate_image(
    db: Session, qr_token: str, image_spec: dict, redis: Redis
) -> bytes | None:
    url_key = f"qr:url:{qr_token}"
    cached_url = redis.get(url_key)
    if cached_url:
        url = cached_url.decode()
    else:
        qr = get_qr_code(db, qr_token)
        if not qr:
            return None
        url = qr.url
        redis.setex(url_key, URL_CACHE_TTL, url)

    spec_hash = compute_spec_hash(image_spec)
    img_key = _img_cache_key(url, image_spec)

    # Use cache_lookup for instrumented hit/miss tracking
    cached_img = cache_lookup(redis, img_key, spec_hash)
    if cached_img:
        return cached_img

    image_bytes = generate_qr_image(url, image_spec)
    redis.setex(img_key, IMG_CACHE_TTL, image_bytes)
    return image_bytes
