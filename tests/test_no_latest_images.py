"""
Guardrail: forbid `:latest` tags on container images across the repo.

Rationale: docs/incidents/2026-05-15-load-test-bootstrap.md
- jaegertracing/all-in-one:latest was retired when Jaeger shipped v2, breaking
  docker-compose for everyone who pulled fresh.
- `:latest` is also a supply-chain risk (the tag can move under you).

Scans Dockerfile, docker-compose*.yml, and .github/workflows/**.yml. Flags any
image reference that ends with `:latest` or omits a tag entirely (Docker treats
no-tag as `:latest`).

Allow-list (explicit opt-out for cases where the tool itself enforces digest
pinning elsewhere, or where the image is documented to not have stable tags):
add the offending line to ALLOWED_UNPINNED below with a clear comment.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files to scan for container image references.
SCAN_PATTERNS: list[str] = [
    "Dockerfile",
    "Dockerfile.*",
    "docker-compose.yml",
    "docker-compose.*.yml",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
]

# Lines that are allowed to use `:latest` or unpinned tags. Add with a justification.
ALLOWED_UNPINNED: set[str] = set()

# Pattern that matches an image reference flagged as unpinned.
# Catches: `image: foo`, `image: foo:latest`, `FROM foo:latest`, `FROM foo` (no tag).
_IMAGE_LINE_RE = re.compile(
    r"""
    ^                                       # line start (multiline scan)
    \s*
    (?:image:\s*|FROM\s+)                   # "image:" (compose) or "FROM" (Dockerfile)
    (?P<image>[\w\-./]+(?::[\w\-.]+)?)      # capture image[:tag]
    """,
    re.VERBOSE | re.MULTILINE,
)


def _iter_scan_targets() -> list[Path]:
    """Resolve glob patterns to actual files that exist."""
    found: list[Path] = []
    for pattern in SCAN_PATTERNS:
        found.extend(REPO_ROOT.glob(pattern))
    return [p for p in found if p.is_file()]


def _is_unpinned(image_ref: str) -> bool:
    """An image is unpinned if it has no tag, or its tag is `latest`."""
    if ":" not in image_ref:
        return True
    _, tag = image_ref.rsplit(":", 1)
    return tag == "latest"


def test_no_latest_or_unpinned_container_images() -> None:
    """All container image references in the repo must pin to an explicit tag."""
    targets = _iter_scan_targets()
    assert targets, "no scan targets found — check SCAN_PATTERNS"

    violations: list[str] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for match in _IMAGE_LINE_RE.finditer(text):
            image = match.group("image")
            if not _is_unpinned(image):
                continue
            line_no = text[: match.start()].count("\n") + 1
            line_content = text.splitlines()[line_no - 1].strip()
            if line_content in ALLOWED_UNPINNED:
                continue
            relative = path.relative_to(REPO_ROOT)
            violations.append(f"{relative}:{line_no}: {line_content}  (image={image!r})")

    assert not violations, (
        "Unpinned or `:latest` container images detected. "
        "Pin an explicit version (e.g. `image:1.62.0`).\n"
        "Rationale: docs/incidents/2026-05-15-load-test-bootstrap.md\n\n"
        + "\n".join(violations)
    )
