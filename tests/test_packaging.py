from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import yaml


def test_dockerfile_includes_runtime_config_assets() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY config ./config" in dockerfile
    assert "COPY docs ./docs" in dockerfile


def test_compose_covers_cpu_gpu_workers_and_docker_access() -> None:
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    assert {"api", "worker-cpu", "worker-gpu", "flower", "redis"} <= set(services)
    assert compose["networks"]["turnstile"]["name"] == "turnstile"
    assert "/var/run/docker.sock:/var/run/docker.sock" in services["api"]["volumes"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in services["worker-gpu"]["volumes"]
    assert "TURNSTILE_RUNTIME_MODE=docker" in services["api"]["environment"]
    assert "TURNSTILE_DOCKER_NETWORK=turnstile" in services["api"]["environment"]
    assert "-Q cpu" in services["worker-cpu"]["command"]
    assert "-Q gpu" in services["worker-gpu"]["command"]
    assert "-A worker:celery_app" in services["worker-cpu"]["command"]
    assert "-A worker:celery_app" in services["flower"]["command"]


def test_runtime_dependencies_include_httpx_and_flower() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]

    assert any(dependency.startswith("httpx") for dependency in dependencies)
    assert any(dependency.startswith("flower") for dependency in dependencies)


def test_example_backend_assets_and_make_targets_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    makefile = (root / "Makefile").read_text(encoding="utf-8")

    assert (root / "examples" / "backends" / "mock_http_tool" / "Dockerfile").exists()
    assert (root / "examples" / "backends" / "mock_http_tool" / "app.py").exists()
    assert (root / "examples" / "backends" / "mock_command_tool" / "Dockerfile").exists()
    assert (root / "examples" / "backends" / "mock_command_tool" / "main.py").exists()
    assert (root / "docker-compose.examples.yml").exists()
    assert (root / "docs" / "smoke-test.md").exists()
    assert (root / "docs" / "testing-backends.md").exists()
    assert (root / "scripts" / "smoke_test.sh").exists()

    for target in (
        "build-example-backends:",
        "build-mock-http-tool:",
        "build-mock-command-tool:",
        "run-mock-http-alpha:",
        "run-mock-http-beta:",
        "smoke-docker:",
        "smoke-docker-keepalive:",
    ):
        assert target in makefile


def test_example_backend_image_tags_are_consistent_in_docs_and_service_yaml() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    smoke_doc = (root / "docs" / "smoke-test.md").read_text(encoding="utf-8")
    testing_doc = (root / "docs" / "testing-backends.md").read_text(encoding="utf-8")
    services_dir = root / "config" / "services"

    http_alpha = yaml.safe_load((services_dir / "mock_http_alpha.yaml").read_text(encoding="utf-8"))
    http_beta = yaml.safe_load((services_dir / "mock_http_beta.yaml").read_text(encoding="utf-8"))
    command_alpha = yaml.safe_load(
        (services_dir / "mock_command_alpha.yaml").read_text(encoding="utf-8")
    )
    command_beta = yaml.safe_load(
        (services_dir / "mock_command_beta.yaml").read_text(encoding="utf-8")
    )

    assert http_alpha["image"] == "turnstile/mock-http-tool:latest"
    assert http_beta["image"] == "turnstile/mock-http-tool:latest"
    assert command_alpha["image"] == "turnstile/mock-command-tool:latest"
    assert command_beta["image"] == "turnstile/mock-command-tool:latest"

    for text in (
        "turnstile/mock-http-tool:latest",
        "turnstile/mock-command-tool:latest",
        "build-example-backends",
        "smoke-docker",
        "/v1/example/http/echo",
        "/v1/example/command/run",
        "service_id",
    ):
        assert text in readme
        assert text in smoke_doc
        assert text in testing_doc


def test_smoke_script_has_valid_shell_syntax() -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "smoke_test.sh"

    result = subprocess.run(
        ["bash", "-n", str(script_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
