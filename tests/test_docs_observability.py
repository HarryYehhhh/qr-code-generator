"""
tests/test_docs_observability.py

Validates that README.md and CLAUDE.md contain required Observability and
Performance sections as specified in Sprint B QA #1 and Sprint C contract.
"""
from pathlib import Path

import pytest

README = Path(__file__).parent.parent / "README.md"
CLAUDE_MD = Path(__file__).parent.parent / "CLAUDE.md"


@pytest.fixture(scope="module")
def readme_content():
    assert README.exists(), f"README.md not found at {README}"
    return README.read_text(encoding="utf-8").lower()


@pytest.fixture(scope="module")
def claude_content():
    assert CLAUDE_MD.exists(), f"CLAUDE.md not found at {CLAUDE_MD}"
    return CLAUDE_MD.read_text(encoding="utf-8")


class TestReadmeObservabilitySection:
    def test_readme_has_observability_section(self, readme_content):
        """README.md must have an Observability section with Jaeger, Prometheus, and Grafana info."""
        assert "observability" in readme_content, (
            "README.md missing 'Observability' section heading"
        )
        # Verify key tool references
        assert "jaeger" in readme_content, "README.md Observability section must mention Jaeger"
        assert "prometheus" in readme_content, "README.md Observability section must mention Prometheus"
        assert "grafana" in readme_content, "README.md Observability section must mention Grafana"
        # Verify port numbers are present
        assert "16686" in readme_content, "README.md must include Jaeger port 16686"
        assert "9090" in readme_content, "README.md must include Prometheus port 9090"
        assert "3000" in readme_content, "README.md must include Grafana port 3000"

    def test_readme_has_performance_section(self, readme_content):
        """README.md must have a Performance section linking to docs/perf-report.md."""
        assert "performance" in readme_content, (
            "README.md missing 'Performance' section heading"
        )
        assert "docs/perf-report.md" in readme_content, (
            "README.md Performance section must link to docs/perf-report.md"
        )


class TestClaudeMdObservabilitySection:
    def test_claude_md_has_observability_section(self, claude_content):
        """CLAUDE.md must reference the three observability modules and ADR-0002."""
        assert "app/observability.py" in claude_content, (
            "CLAUDE.md missing reference to 'app/observability.py'"
        )
        assert "app/logging.py" in claude_content, (
            "CLAUDE.md missing reference to 'app/logging.py'"
        )
        assert "app/metrics.py" in claude_content, (
            "CLAUDE.md missing reference to 'app/metrics.py'"
        )
        assert "ADR-0002" in claude_content, (
            "CLAUDE.md missing reference to 'ADR-0002'"
        )
