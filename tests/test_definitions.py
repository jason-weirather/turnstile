from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import ValidationError

from app.services.definition_loader import DefinitionLoader


def test_invalid_capability_definition_fails_fast(tmp_path: Path) -> None:
    capabilities_dir = tmp_path / "capabilities"
    capabilities_dir.mkdir()
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    schemas_dir = Path(__file__).resolve().parents[1] / "config" / "schemas"

    (capabilities_dir / "broken.yaml").write_text(
        "capability_id: broken.capability\nmethod: POST\n",
        encoding="utf-8",
    )

    loader = DefinitionLoader(
        capabilities_dir=capabilities_dir,
        services_dir=services_dir,
        schemas_dir=schemas_dir,
    )

    with pytest.raises(ValidationError):
        loader.load_capabilities()


def test_repo_config_examples_load_and_validate() -> None:
    root = Path(__file__).resolve().parents[1]
    loader = DefinitionLoader(
        capabilities_dir=root / "config" / "capabilities",
        services_dir=root / "config" / "services",
        schemas_dir=root / "config" / "schemas",
    )

    capabilities = loader.load_capabilities()
    services = loader.load_services()

    assert {capability.capability_id for capability in capabilities} >= {
        "example.http.echo",
        "example.command.run",
        "image.generate",
        "audio.transcribe",
    }
    assert {service.service_id for service in services} >= {
        "mock-http-alpha",
        "mock-http-beta",
        "mock-command-alpha",
        "mock-image-generator",
        "mock-audio-transcriber",
    }


def test_generic_example_services_reuse_shared_backend_images() -> None:
    root = Path(__file__).resolve().parents[1]
    loader = DefinitionLoader(
        capabilities_dir=root / "config" / "capabilities",
        services_dir=root / "config" / "services",
        schemas_dir=root / "config" / "schemas",
    )

    capabilities = {
        capability.capability_id: capability for capability in loader.load_capabilities()
    }
    services = {service.service_id: service for service in loader.load_services()}

    assert capabilities["example.http.echo"].path == "/example/http/echo"
    assert capabilities["example.command.run"].path == "/example/command/run"

    assert services["mock-http-alpha"].image == "turnstile/mock-http-tool:latest"
    assert services["mock-http-beta"].image == "turnstile/mock-http-tool:latest"
    assert services["mock-http-alpha"].capabilities == ["example.http.echo"]
    assert services["mock-http-beta"].capabilities == ["example.http.echo"]

    assert services["mock-command-alpha"].image == "turnstile/mock-command-tool:latest"
    assert services["mock-command-beta"].image == "turnstile/mock-command-tool:latest"
    assert services["mock-command-alpha"].capabilities == ["example.command.run"]
    assert services["mock-command-beta"].capabilities == ["example.command.run"]
