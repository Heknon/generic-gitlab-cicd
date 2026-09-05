"""Record the exact source-built toolkit wheel for the offline image layer."""
import hashlib
from pathlib import Path

from packaging.utils import parse_wheel_filename

directory = Path("images/python/wheelhouse")
wheels = list(directory.glob("generic_gitlab_ci-*.whl"))
if len(wheels) != 1:
    raise SystemExit("expected exactly one toolkit wheel; clean old factory output first")
wheel = wheels[0]
name, version, _, _ = parse_wheel_filename(wheel.name)
sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
(directory / "toolkit-requirements.txt").write_text(f"{name}=={version} --hash=sha256:{sha}\n")
