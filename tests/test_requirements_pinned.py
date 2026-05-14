"""
tests/test_requirements_pinned.py

Validates that all observability packages in requirements.txt are pinned
to a specific version using the == operator (e.g. opentelemetry-sdk==1.27.0).
"""
import re
from pathlib import Path

REQUIREMENTS_FILE = Path(__file__).parent.parent / "requirements.txt"

# The 10 observability packages that must be pinned.
OBSERVABILITY_PACKAGES = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
    "opentelemetry-exporter-gcp-trace",
    "opentelemetry-instrumentation-fastapi",
    "opentelemetry-instrumentation-sqlalchemy",
    "opentelemetry-instrumentation-redis",
    "opentelemetry-instrumentation-requests",
    "prometheus-fastapi-instrumentator",
    "prometheus-client",
    "structlog",
]


class TestObservabilityPackagesPinned:
    def test_observability_packages_pinned(self):
        """All observability packages must use == version pinning in requirements.txt."""
        assert REQUIREMENTS_FILE.exists(), f"requirements.txt not found at {REQUIREMENTS_FILE}"
        content = REQUIREMENTS_FILE.read_text(encoding="utf-8")

        # Build a map of package_name_lower -> line for packages that have == pinning
        pinned = {}
        pin_pattern = re.compile(
            r"^([A-Za-z0-9_\-]+)==(\d[\w.\-]*)",
            re.MULTILINE,
        )
        for match in pin_pattern.finditer(content):
            pkg = match.group(1).lower().replace("_", "-")
            pinned[pkg] = match.group(0)

        not_pinned = []
        for pkg in OBSERVABILITY_PACKAGES:
            normalized = pkg.lower().replace("_", "-")
            if normalized not in pinned:
                not_pinned.append(pkg)

        assert not not_pinned, (
            "The following observability packages are missing '==X.Y.Z' pinning in requirements.txt:\n"
            + "\n".join(f"  - {p}" for p in not_pinned)
        )
