"""Minimal generator fixture: replace the body with the chosen OpenAPI generator."""
import json
from pathlib import Path
root = Path("generated")
(root / "sdk/sdk").mkdir(parents=True, exist_ok=True)
(root / "openapi.json").write_text(json.dumps({"openapi": "3.1.0", "info": {"title": "Example", "version": "1.0.0"}, "paths": {}}))
(root / "sdk/sdk/__init__.py").write_text('VERSION = "1.0.0"\n')
(root / "sdk/pyproject.toml").write_text(Path("sdk-pyproject.toml").read_text())
