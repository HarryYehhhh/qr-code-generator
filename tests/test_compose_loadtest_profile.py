"""
tests/test_compose_loadtest_profile.py

Validates docker-compose.yml structure for the k6 loadtest profile.
Parses the YAML directly (no Docker daemon required) for most assertions;
optionally runs docker compose config when docker is available.

After ADR-0004 the prometheus / grafana / jaeger services were removed,
so the only remaining loadtest-related assertion is on the k6 service.
"""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

COMPOSE_FILE = Path(__file__).parent.parent / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose_data():
    assert COMPOSE_FILE.exists(), f"docker-compose.yml not found at {COMPOSE_FILE}"
    with COMPOSE_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestComposeLoadtestProfile:
    def test_compose_loadtest_config_parses(self):
        """If docker is available, docker compose --profile loadtest config must succeed."""
        docker_path = shutil.which("docker")
        if docker_path is None:
            pytest.skip("docker binary not found in PATH; skipping compose config check")

        result = subprocess.run(
            [docker_path, "compose", "-f", str(COMPOSE_FILE),
             "--profile", "loadtest", "config"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"docker compose --profile loadtest config failed:\n{result.stderr}"
        )

    def test_compose_yaml_has_k6_service_in_loadtest_profile(self, compose_data):
        """docker-compose.yml must define a k6 service with profile=loadtest, pinned image, and depends_on api."""
        services = compose_data.get("services", {})
        assert "k6" in services, "docker-compose.yml missing 'k6' service"

        k6 = services["k6"]
        # Image must be grafana/k6 and pinned (no `:latest`, no missing tag).
        image = k6.get("image", "")
        assert image.startswith("grafana/k6:"), (
            f"k6 service image must be grafana/k6 with an explicit tag, got {image!r}"
        )
        tag = image.split(":", 1)[1] if ":" in image else ""
        assert tag and tag != "latest", (
            f"k6 service image must pin a specific version (not `:latest`), got {image!r}"
        )
        assert k6.get("profiles") == ["loadtest"], (
            f"k6 service profiles should be ['loadtest'], got {k6.get('profiles')!r}"
        )

        # depends_on can be a list or a dict (Compose v3 condition syntax)
        depends_on = k6.get("depends_on", [])
        if isinstance(depends_on, dict):
            depends_keys = list(depends_on.keys())
        else:
            depends_keys = list(depends_on)

        assert "api" in depends_keys, f"k6 service must depend_on 'api'; got {depends_keys}"
