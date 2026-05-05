import ipaddress
from urllib.parse import urlparse


def validate_safe_url(url: str) -> None:
    """Raise ValueError if URL fails MVP safety checks.

    Pre-conditions: caller has already enforced HTTP(S) scheme, ASCII, length.
    """
    parsed = urlparse(url)

    # Userinfo trick: http://google.com@evil.com displays as google.com
    if "@" in parsed.netloc:
        raise ValueError("URL must not contain user credentials")

    host = (parsed.hostname or "").lower()

    if host == "localhost":
        raise ValueError("URL must not target localhost")

    # IP-literal host targeting non-public address space → SSRF risk
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # not an IP literal, normal hostname is fine
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError("URL must not target a private or loopback address")
