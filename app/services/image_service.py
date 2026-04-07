import hashlib
import io
import json

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def compute_spec_hash(image_spec: dict) -> str:
    normalized = json.dumps(image_spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def generate_qr_image(url: str, image_spec: dict) -> bytes:
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
