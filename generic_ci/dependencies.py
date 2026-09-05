"""Temporary uv environments with workspace ownership and immutable candidates."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

import tomlkit
from packaging.requirements import Requirement

from .config import allowed_url
from .models import relative


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def resolve_candidates(environment, hosts, projects):
    single = [environment.get("CI_DEPENDENCY_" + key, "") for key in ("REPO", "REF", "PACKAGE")]
    raw = environment.get("CI_DEPENDENCY_OVERRIDES", "[]") or "[]"
    filename = environment.get("CI_DEPENDENCY_FILE", "")
    decoded = json.loads(raw)
    if not isinstance(decoded, list):
        raise ValueError("CI_DEPENDENCY_OVERRIDES must be a JSON array")
    if sum([bool(any(single)), bool(decoded), bool(filename)]) > 1:
        raise ValueError("provide only one override input: single fields, JSON, or file")
    if filename:
        decoded = json.loads(Path(filename).read_text())
    if any(single):
        if not all(single):
            raise ValueError("provide repository, ref and package together")
        decoded = [dict(zip(("repository", "ref", "package"), single),
                        subdirectory=environment.get("CI_DEPENDENCY_SUBDIRECTORY", ""))]
    if not isinstance(decoded, list):
        raise ValueError("dependency file must contain a JSON array")
    seen, result = set(), []
    for item in decoded:
        if not isinstance(item, dict) or set(item) - {"repository", "repo", "ref", "package", "subdirectory", "projects"}:
            raise ValueError("override fields: repository, ref, package, subdirectory, projects")
        if "repo" in item and "repository" in item:
            raise ValueError("use repository or legacy repo, not both")
        repo, ref, name = item.get("repository", item.get("repo", "")), item.get("ref", ""), item.get("package", "")
        if not all(isinstance(v, str) for v in (repo, ref, name)):
            raise ValueError("override repository/ref/package must be strings")
        allowed_url(repo, hosts)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError("invalid override distribution name")
        if normalize(name) in seen:
            raise ValueError(f"duplicate override package {name}")
        seen.add(normalize(name))
        if not ref or ref.startswith("-") or re.search(r"[\s~^:?*\[\\]", ref):
            raise ValueError("invalid dependency ref")
        sub = item.get("subdirectory", "")
        if sub:
            relative(sub)
        scope = item.get("projects", list(projects))
        if not isinstance(scope, list) or not scope or any(p not in projects for p in scope):
            raise ValueError("override projects must name existing projects")
        if re.fullmatch(r"[a-fA-F0-9]{40}", ref):
            sha = ref.lower()
        else:
            wanted = [ref] if ref.startswith("refs/") else ["refs/heads/" + ref, "refs/tags/" + ref]
            output = subprocess.check_output(["git", "ls-remote", "--exit-code", repo, *wanted, *[v + "^{}" for v in wanted]], text=True)
            matches = dict(line.split()[::-1] for line in output.splitlines())
            found = [v for v in wanted if v in matches]
            if len(found) != 1:
                raise ValueError("ambiguous/missing dependency ref; use refs/heads/... or refs/tags/...")
            sha = matches.get(found[0] + "^{}", matches[found[0]])
        result.append({"package": name, "repository": repo, "requested_ref": ref, "commit": sha,
                       "subdirectory": sub, "projects": scope})
    return result


def workspace(directory, repository):
    directory, repository = Path(directory).resolve(), Path(repository).resolve()
    directory.relative_to(repository)
    root = directory
    for parent in (directory, *directory.parents):
        if not parent.is_relative_to(repository):
            break
        manifest = parent / "pyproject.toml"
        if manifest.is_file():
            data = tomllib.loads(manifest.read_text())
            settings = data.get("tool", {}).get("uv", {}).get("workspace")
            if settings:
                members = {p.resolve() for pattern in settings.get("members", []) for p in parent.glob(pattern)}
                excluded = {p.resolve() for pattern in settings.get("exclude", []) for p in parent.glob(pattern)}
                if directory == parent or directory in members - excluded:
                    root = parent
                    break
    return root


def validate_indexes(root, hosts):
    data = tomllib.loads((root / "pyproject.toml").read_text())
    uv = data.get("tool", {}).get("uv", {})
    for index in uv.get("index", []):
        for key in ("url", "publish-url"):
            if key in index:
                allowed_url(index[key], hosts)
    for key in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL"):
        for url in os.environ.get(key, "").split():
            allowed_url(url, hosts)
    for dependency in data.get("project", {}).get("dependencies", []):
        url = Requirement(dependency).url
        if url and not url.startswith("file:"):
            allowed_url(url.removeprefix("git+"), hosts)
    return data


@contextlib.contextmanager
def prepared(directory, repository, policy, candidates, project, hosts, record_dir):
    """Keep temporary metadata active until caller completes; always restore it."""
    if policy["manager"] == "none":
        yield {"manager": "none"}
        return
    root = workspace(directory, repository)
    manifest, lock = root / "pyproject.toml", root / "uv.lock"
    if not lock.is_file():
        raise ValueError(f"commit workspace lockfile {lock}")
    validate_indexes(root, hosts)
    original, original_lock = manifest.read_bytes(), lock.read_bytes()
    selected = [r for r in candidates if project in r["projects"]]
    options = ["--no-default-groups"]
    if "*" in policy["groups"]:
        options += ["--all-groups"]
    else:
        for group in policy["groups"]:
            options += ["--group", group]
    for extra in policy["extras"]:
        options += ["--extra", extra]
    if Path(directory).resolve() != root:
        member = tomllib.loads((Path(directory) / "pyproject.toml").read_text())["project"]["name"]
        options += ["--package", member]
    environment = {**os.environ, "UV_PYTHON_DOWNLOADS": "never", "UV_PROJECT_ENVIRONMENT": str(root / ".venv")}
    python_options = ["--python", policy["python"]] if policy.get("python") else []
    try:
        data = tomlkit.parse(original.decode())
        uv = data.setdefault("tool", {}).setdefault("uv", {})
        if selected:
            names = {normalize(r["package"]) for r in selected}
            previous = uv.get("override-dependencies", [])
            if any(not isinstance(v, str) for v in previous):
                raise ValueError("scoped uv overrides require an explicit adapter; string overrides supported")
            uv["override-dependencies"] = [v for v in previous if normalize(Requirement(v).name) not in names] + [
                f"{r['package']} @ git+{r['repository']}@{r['commit']}" + (f"#subdirectory={r['subdirectory']}" if r["subdirectory"] else "") for r in selected]
            for key in list(uv.get("sources", {})):
                if normalize(key) in names:
                    del uv["sources"][key]
            manifest.write_text(tomlkit.dumps(data))
        upgrade = policy["upgrade"]
        upgrade_options = ["--upgrade"] if upgrade == "all" else []
        if isinstance(upgrade, list):
            for package in upgrade:
                upgrade_options += ["--upgrade-package", package]
        if selected or upgrade != "none":
            subprocess.run(["uv", "lock", *upgrade_options, *python_options], cwd=root, env=environment, check=True)
        subprocess.run(["uv", "sync", "--locked", *options, *python_options], cwd=root, env=environment, check=True)
        interpreter = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        actual = subprocess.check_output([str(interpreter), "-c", "import platform; print(platform.python_version())"], text=True).strip()
        if policy.get("python") and not (actual == policy["python"] or actual.startswith(policy["python"] + ".")):
            raise ValueError(f"requested Python {policy['python']}; got {actual}")
        for candidate in selected:
            code = "import importlib.metadata as m; print(m.distribution(__import__('sys').argv[1]).read_text('direct_url.json') or '{}')"
            source = json.loads(subprocess.check_output([str(interpreter), "-c", code, candidate["package"]], text=True))
            if source.get("vcs_info", {}).get("commit_id") != candidate["commit"] or source.get("url", "").rstrip("/") != candidate["repository"].rstrip("/"):
                raise ValueError(f"installed {candidate['package']} does not match candidate repository/commit")
        record_dir = Path(record_dir)
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / "uv.lock").write_bytes(lock.read_bytes())
        record = {"manager": "uv", "python": actual, "workspace": root.relative_to(repository).as_posix(),
                  "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(), "candidates": selected,
                  "policy": policy, "interpreter": str(interpreter)}
        yield record
    finally:
        manifest.write_bytes(original)
        lock.write_bytes(original_lock)
