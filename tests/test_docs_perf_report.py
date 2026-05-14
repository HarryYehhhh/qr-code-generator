"""
tests/test_docs_perf_report.py

Validates the structure and completeness of docs/perf-report.md.
Checks required section headings, scenario sub-sections, and that every
<TBD: placeholder has a Run: or Source: annotation within 200 characters.
"""
import re
from pathlib import Path

import pytest

PERF_REPORT = Path(__file__).parent.parent / "docs" / "perf-report.md"


@pytest.fixture(scope="module")
def report_content():
    assert PERF_REPORT.exists(), f"docs/perf-report.md not found at {PERF_REPORT}"
    return PERF_REPORT.read_text(encoding="utf-8")


class TestPerfReportSections:
    def test_perf_report_has_required_sections(self, report_content):
        """docs/perf-report.md must contain all required top-level section headings."""
        required = [
            "# Performance Report",
            "## 1. Environment",
            "## 2. Scenarios",
            "## 3. Baseline vs Current",
            "## 4. Bottleneck",
            "## 5. Conclusion",
        ]
        for heading in required:
            assert heading in report_content, (
                f"docs/perf-report.md missing required section: {heading!r}"
            )

    def test_perf_report_has_scenario_subsections(self, report_content):
        """docs/perf-report.md must contain subsection headings for all three scenarios."""
        required_subsections = [
            "### 2.1 redirect_hot",
            "### 2.2 redirect_cold",
            "### 2.3 image_mixed",
        ]
        for subsection in required_subsections:
            assert subsection in report_content, (
                f"docs/perf-report.md missing scenario subsection: {subsection!r}"
            )

    def test_perf_report_placeholders_have_source(self, report_content):
        """Every <TBD: placeholder must have a Run: or Source: annotation within 200 characters."""
        # Find all occurrences of <TBD:
        tbd_pattern = re.compile(r"<TBD:")
        violations = []

        for match in tbd_pattern.finditer(report_content):
            start = match.start()
            # Look in the 200 characters following the placeholder
            window = report_content[start : start + 200]
            has_run = "Run:" in window
            has_source = "Source:" in window
            if not (has_run or has_source):
                # Get surrounding line for context
                line_start = report_content.rfind("\n", 0, start) + 1
                line_end = report_content.find("\n", start)
                line = report_content[line_start:line_end].strip()
                violations.append(f"  Position {start}: {line[:80]!r}")

        assert not violations, (
            "The following <TBD: placeholders are missing a Run: or Source: annotation "
            "within 200 characters:\n" + "\n".join(violations)
        )
