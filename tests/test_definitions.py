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
