"""
tests/test_k6_scripts.py

Validates structural correctness of k6 load-test scripts without running k6.
Checks file existence, required exports, and optionally runs `node --check` for
JavaScript syntax validation when node is available in the environment.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts" / "k6"

# Map of script path -> (description, required patterns)
SCRIPTS = {
    "seed.js": {
        "description": "seed script",
        "patterns": ["export default", "export function handleSummary"],
        "require_any": True,  # at least one of patterns must match
    },
    "redirect_hot.js": {
        "description": "redirect_hot script",
        "patterns": ["export const options", "thresholds", "scenarios", "lib/common"],
        "require_any": False,
    },
    "redirect_cold.js": {
        "description": "redirect_cold script",
        "patterns": ["export const options", "thresholds", "scenarios", "lib/common"],
        "require_any": False,
    },
    "image_mixed.js": {
        "description": "image_mixed script",
        "patterns": ["export const options", "thresholds", "scenarios", "lib/common"],
        "require_any": False,
    },
    "lib/common.js": {
        "description": "common lib",
        "patterns": [
            "export function baseUrl",
            "export const defaultOptions",
            "export function buildThresholds",
            "export function pickToken",
        ],
        "require_any": False,
    },
}


def _read_script(relative_path: str) -> str:
    path = SCRIPTS_DIR / relative_path
    assert path.exists(), f"Script not found: {path}"
    return path.read_text(encoding="utf-8")


class TestSeedScript:
    def test_seed_script_exists_and_has_default_export(self):
        """seed.js must exist and expose at least one of: export default / export function handleSummary."""
        content = _read_script("seed.js")
        has_default = "export default" in content
        has_handle_summary = "export function handleSummary" in content
        assert has_default or has_handle_summary, (
            "seed.js must contain 'export default' or 'export function handleSummary'"
        )

    def test_seed_script_has_no_python_syntax(self):
        """seed.js must not contain Python-style syntax markers like 'def ' or Python print()."""
        content = _read_script("seed.js")
        # Only check for clear Python-style 'def ' function definitions (not valid in JS)
        for line in content.splitlines():
            stripped = line.strip()
            assert not stripped.startswith("def "), f"Found Python-style 'def' in seed.js: {line!r}"


class TestRedirectHotScript:
    def test_redirect_hot_has_options_and_thresholds(self):
        """redirect_hot.js must have options, thresholds, scenarios, and import lib/common."""
        content = _read_script("redirect_hot.js")
        assert "export const options" in content, "redirect_hot.js missing 'export const options'"
        assert "thresholds" in content, "redirect_hot.js missing 'thresholds'"
        assert "scenarios" in content, "redirect_hot.js missing 'scenarios'"
        assert "lib/common" in content, "redirect_hot.js must import from lib/common"

    def test_redirect_hot_has_default_function(self):
        """redirect_hot.js must have a default export function."""
        content = _read_script("redirect_hot.js")
        assert "export default function" in content, "redirect_hot.js missing 'export default function'"


class TestRedirectColdScript:
    def test_redirect_cold_has_options_and_thresholds(self):
        """redirect_cold.js must have options, thresholds, scenarios, and import lib/common."""
        content = _read_script("redirect_cold.js")
        assert "export const options" in content, "redirect_cold.js missing 'export const options'"
        assert "thresholds" in content, "redirect_cold.js missing 'thresholds'"
        assert "scenarios" in content, "redirect_cold.js missing 'scenarios'"
        assert "lib/common" in content, "redirect_cold.js must import from lib/common"

    def test_redirect_cold_has_default_function(self):
        """redirect_cold.js must have a default export function."""
        content = _read_script("redirect_cold.js")
        assert "export default function" in content, "redirect_cold.js missing 'export default function'"


class TestImageMixedScript:
    def test_image_mixed_has_options_and_thresholds(self):
        """image_mixed.js must have options, thresholds, scenarios, and import lib/common."""
        content = _read_script("image_mixed.js")
        assert "export const options" in content, "image_mixed.js missing 'export const options'"
        assert "thresholds" in content, "image_mixed.js missing 'thresholds'"
        assert "scenarios" in content, "image_mixed.js missing 'scenarios'"
        assert "lib/common" in content, "image_mixed.js must import from lib/common"

    def test_image_mixed_has_default_function(self):
        """image_mixed.js must have a default export function."""
        content = _read_script("image_mixed.js")
        assert "export default function" in content, "image_mixed.js missing 'export default function'"


class TestCommonLib:
    def test_common_lib_exports(self):
        """lib/common.js must export baseUrl, defaultOptions, buildThresholds, and pickToken."""
        content = _read_script("lib/common.js")
        assert "export function baseUrl" in content, "lib/common.js missing 'export function baseUrl'"
        assert "export const defaultOptions" in content, "lib/common.js missing 'export const defaultOptions'"
        assert "export function buildThresholds" in content, "lib/common.js missing 'export function buildThresholds'"
        assert "export function pickToken" in content, "lib/common.js missing 'export function pickToken'"


class TestNodeSyntaxCheck:
    def test_node_syntax_check_when_available(self):
        """If node is available, run node --check on all 5 JS files; skip if node is missing."""
        node_path = shutil.which("node")
        if node_path is None:
            pytest.skip("node binary not found in PATH; skipping JS syntax check")

        js_files = [
            SCRIPTS_DIR / "seed.js",
            SCRIPTS_DIR / "redirect_hot.js",
            SCRIPTS_DIR / "redirect_cold.js",
            SCRIPTS_DIR / "image_mixed.js",
            SCRIPTS_DIR / "lib" / "common.js",
        ]

        failed = []
        for js_file in js_files:
            result = subprocess.run(
                [node_path, "--check", str(js_file)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                failed.append(f"{js_file.name}: {result.stderr.strip()}")

        assert not failed, "node --check failed for:\n" + "\n".join(failed)
