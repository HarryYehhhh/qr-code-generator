import hashlib
import io
import json

import qrcode
from opentelemetry import trace
from qrcode.constants import ERROR_CORRECT_M

_tracer = trace.get_tracer(__name__)


def compute_spec_hash(image_spec: dict) -> str:
    normalized = json.dumps(image_spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def generate_qr_image(url: str, image_spec: dict) -> bytes:
    spec_hash = compute_spec_hash(image_spec)
    with _tracer.start_as_current_span("image_service.generate") as span:
        span.set_attribute("qr.image.spec_hash", spec_hash)

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
        return buf.getvalue()


def cache_lookup(redis_client, img_key: str, spec_hash: str):
    """Look up the image cache and record hit/miss span attributes + metrics.

    Returns cached bytes or None on miss.
    """
    from app.metrics import observe_image_cache

    with _tracer.start_as_current_span("image_service.cache_lookup") as span:
        span.set_attribute("qr.image.spec_hash", spec_hash)
        cached = redis_client.get(img_key)
        if cached:
            span.set_attribute("qr.cache_result", "hit")
            observe_image_cache("hit")
            return cached
        else:
            span.set_attribute("qr.cache_result", "miss")
            observe_image_cache("miss")
            return None
