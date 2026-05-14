"""
tests/test_compose_loadtest_profile.py

Validates docker-compose.yml structure for the k6 loadtest profile.
Parses the YAML directly (no Docker daemon required) for most assertions;
optionally runs docker compose config when docker is available.
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
        """docker-compose.yml must define a k6 service with profile=loadtest, correct image, and depends_on."""
        services = compose_data.get("services", {})
        assert "k6" in services, "docker-compose.yml missing 'k6' service"

        k6 = services["k6"]
        assert k6.get("image") == "grafana/k6:latest", (
            f"k6 service image should be 'grafana/k6:latest', got {k6.get('image')!r}"
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
        assert "prometheus" in depends_keys, (
            f"k6 service must depend_on 'prometheus'; got {depends_keys}"
        )

    def test_prometheus_remote_write_enabled(self, compose_data):
        """prometheus service command must include --web.enable-remote-write-receiver."""
        services = compose_data.get("services", {})
        assert "prometheus" in services, "docker-compose.yml missing 'prometheus' service"

        prometheus = services["prometheus"]
        command = prometheus.get("command", [])

        # command may be a list or a string
        if isinstance(command, str):
            command_str = command
        else:
            command_str = " ".join(command)

        assert "--web.enable-remote-write-receiver" in command_str, (
            "prometheus service command must include '--web.enable-remote-write-receiver' "
            f"for k6 remote write support. Got: {command_str!r}"
        )
