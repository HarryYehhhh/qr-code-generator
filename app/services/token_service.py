import hashlib
import os
import string

BASE62_CHARS = string.digits + string.ascii_letters


def _base62_encode(num: int) -> str:
    if num == 0:
        return BASE62_CHARS[0]
    result = []
    while num > 0:
        result.append(BASE62_CHARS[num % 62])
        num //= 62
    return "".join(reversed(result))


def generate_qr_token(url: str, server_secret: str) -> str:
    nonce = os.urandom(16).hex()
    raw = f"{url}{nonce}{server_secret}"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    num = int.from_bytes(digest[:16], "big")
    encoded = _base62_encode(num)
    return encoded[:10]
