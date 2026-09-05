from pathlib import Path
import json
assert json.loads(Path("generated/openapi.json").read_text())["openapi"] == "3.1.0"
assert Path("generated/sdk/sdk/__init__.py").is_file()
