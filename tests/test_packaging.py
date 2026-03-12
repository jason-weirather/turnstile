from __future__ import annotations

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
