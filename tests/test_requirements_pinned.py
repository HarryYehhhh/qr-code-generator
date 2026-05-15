"""
tests/test_requirements_pinned.py

Validates that core dependencies in requirements.txt are pinned to a specific
version using the == operator. After ADR-0004, only structlog remains from
the observability stack.
"""
import re
from pathlib import Path

REQUIREMENTS_FILE = Path(__file__).parent.parent / "requirements.txt"

# Core packages that must be pinned to maintain reproducible builds.
PACKAGES_THAT_MUST_BE_PINNED = [
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "alembic",
    "pydantic-settings",
    "qrcode",
    "redis",
    "structlog",
]


class TestRequirementsPinned:
    def test_core_packages_pinned(self):
        """All core packages must use == version pinning in requirements.txt."""
        assert REQUIREMENTS_FILE.exists(), f"requirements.txt not found at {REQUIREMENTS_FILE}"
        content = REQUIREMENTS_FILE.read_text(encoding="utf-8")

        pinned = set()
        pin_pattern = re.compile(
            r"^([A-Za-z0-9_\-]+)(?:\[[^\]]+\])?==(\d[\w.\-]*)",
            re.MULTILINE,
        )
        for match in pin_pattern.finditer(content):
            pinned.add(match.group(1).lower().replace("_", "-"))

        not_pinned = [
            pkg for pkg in PACKAGES_THAT_MUST_BE_PINNED
            if pkg.lower().replace("_", "-") not in pinned
        ]
        assert not not_pinned, (
            "The following core packages are missing '==X.Y.Z' pinning in requirements.txt:\n"
            + "\n".join(f"  - {p}" for p in not_pinned)
        )
