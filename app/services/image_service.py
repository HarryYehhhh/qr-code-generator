import hashlib
import io
import json

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from app.logging import get_logger

logger = get_logger(__name__)


def compute_spec_hash(image_spec: dict) -> str:
    normalized = json.dumps(image_spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def generate_qr_image(url: str, image_spec: dict) -> bytes:
    spec_hash = compute_spec_hash(image_spec)

    dimension = image_spec.get("dimension", 256)
    color = image_spec.get("color", "#000000")
    border = image_spec.get("border", 4)

    modules = 21 + 2 * border
    box_size = max(1, dimension // modules)

    qr = qrcode.QRCode(
        version=1,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=color, back_color="white")
    img = img.resize((dimension, dimension))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    logger.info("image_generated", spec_hash=spec_hash, dimension=dimension)
    return buf.getvalue()


def cache_lookup(redis_client, img_key: str, spec_hash: str):
    """Look up the image cache. Returns cached bytes or None on miss."""
    cached = redis_client.get(img_key)
    if cached:
        logger.info("image_cache", spec_hash=spec_hash, result="hit")
        return cached
    logger.info("image_cache", spec_hash=spec_hash, result="miss")
    return None
