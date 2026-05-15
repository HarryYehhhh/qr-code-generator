"""
tests/test_docs_perf_report.py

Validates the structure and completeness of docs/perf-report.md (the clean
final three-architecture load-test report). Checks required section
headings, that all three scenarios and all three architectures are present,
and that the report is free of stale-draft markers.
"""
from pathlib import Path

import pytest

PERF_REPORT = Path(__file__).parent.parent / "docs" / "perf-report.md"


@pytest.fixture(scope="module")
def report_content():
    assert PERF_REPORT.exists(), f"docs/perf-report.md not found at {PERF_REPORT}"
    return PERF_REPORT.read_text(encoding="utf-8")


class TestPerfReportSections:
    def test_perf_report_has_required_sections(self, report_content):
        """Must contain all required top-level section headings."""
        required = [
            "# Performance Report",
            "## Environment",
            "## Architectures compared",
            "## Scenarios",
            "## Results",
            "## Bottleneck analysis",
            "## Conclusion",
        ]
        for heading in required:
            assert heading in report_content, (
                f"docs/perf-report.md missing required section: {heading!r}"
            )

    def test_perf_report_covers_all_scenarios(self, report_content):
        """All three load-test scenarios must be reported."""
        for scenario in ("redirect_hot", "redirect_cold", "image_mixed"):
            assert scenario in report_content, (
                f"docs/perf-report.md missing scenario: {scenario!r}"
            )

    def test_perf_report_covers_all_architectures(self, report_content):
        """All three compared architectures must be present."""
        for arch in ("同步點擊", "異步 worker", "無 click"):
            assert arch in report_content, (
                f"docs/perf-report.md missing architecture: {arch!r}"
            )

    def test_perf_report_has_no_stale_draft_markers(self, report_content):
        """The final report must not contain placeholders or removed-stack refs."""
        for marker in ("<TBD:", "Baseline vs Current", "Prometheus", "Jaeger"):
            assert marker not in report_content, (
                f"docs/perf-report.md still contains stale marker: {marker!r}"
            )
