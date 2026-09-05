"""Job execution and receipts. No runtime dependency/tool downloads are bootstrapped."""
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

from packaging.version import Version
import yaml

from . import __version__
from .compiler import digest
from .config import affected, allowed_url
from .dependencies import prepared, resolve_candidates


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def path_in(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"path escapes repository: {relative}")
    return path


def identity(config_hash):
    return {"config": config_hash, "commit": os.environ.get("CI_COMMIT_SHA", "local"),
            "pipeline": os.environ.get("CI_PIPELINE_ID", "local")}


def load_config(expected):
    data = json.loads(base64.b64decode(os.environ["TOOLKIT_CONFIG_B64"], validate=True))
    if digest(data) != expected:
        raise ValueError("generated configuration fingerprint mismatch")
    if data["version"] != __version__:
        raise ValueError(f"runtime {__version__} does not match compiler {data['version']}; rebuild internal runtime image")
    return data


def make_plan(data, root, expected):
    for path, sha in data["sources"].items():
        if hashlib.sha256(path_in(root, path).read_bytes()).hexdigest() != sha:
            raise ValueError(f"{path} changed: run generic-ci render and commit generated CI")
    full = os.environ.get("CI_FULL_PIPELINE") == "true" or bool(os.environ.get("CI_COMMIT_TAG"))
    full |= os.environ.get("CI_PIPELINE_SOURCE") in {"web", "api", "schedule", "trigger", "pipeline"}
    changed = None
    base = os.environ.get("CI_MERGE_REQUEST_DIFF_BASE_SHA") or os.environ.get("CI_COMMIT_BEFORE_SHA", "")
    if not full and re.fullmatch(r"[0-9a-f]{40}", base) and set(base) != {"0"}:
        result = subprocess.run(["git", "diff", "--name-only", "--no-renames", base, "HEAD"], cwd=root, capture_output=True, text=True)
        if result.returncode == 0:
            changed = result.stdout.splitlines()
            if set(changed) & set(data["sources"]):
                changed = None
    candidates = resolve_candidates(os.environ, data["platform"]["allowed_hosts"], data["pipeline"]["projects"])
    selected = affected(data["pipeline"], changed)
    tag = os.environ.get("CI_COMMIT_TAG", "")
    if tag:
        release_projects = {name: p["release"] for name, p in data["pipeline"]["projects"].items() if p["release"]}
        if release_projects:
            selected = {name for name, r in release_projects.items() if re.fullmatch(re.escape(r["tag"]).replace(re.escape("{version}"), ".+"), tag)}
            if not selected:
                raise ValueError("tag does not match any declared project release")
    for candidate in candidates:
        selected.update(candidate["projects"])
    # Required producer work must exist even when only a consumer changed.
    # Shared deployments rebuild the entire declared image set, preserving workloads.
    while True:
        expanded = selected | {data["nodes"][upstream]["project"] for node in data["nodes"].values()
                               if node["project"] in selected for upstream in node["needs"]}
        if expanded == selected:
            break
        selected = expanded
    plan = {**identity(expected), "selected": sorted(selected), "candidates": candidates, "created": time.time()}
    write_json(root / ".ci-out/plan.json", plan)
    print(json.dumps({"selected": plan["selected"], "candidates": candidates}, indent=2))


def receipt_path(root, job):
    return root / ".ci-out" / job / "receipt.json"


def require_receipt(root, job, expected):
    path = receipt_path(root, job)
    if not path.is_file():
        raise ValueError(f"required job {job} has no success receipt (skipped, failed, or expired artifacts)")
    record = json.loads(path.read_text())
    if any(record.get(key) != value for key, value in identity(expected).items()) or record.get("status") != "passed":
        raise ValueError(f"required job {job} has stale or unsuccessful evidence")
    for artifact in record.get("files", []):
        source = path_in(root, artifact["artifact"])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError(f"artifact missing or modified: {source}")
    return record


def commands(lines, cwd, shell, environment=None):
    environment = {**os.environ, **(environment or {})}
    if not lines:
        return
    if shell == "powershell":
        script = "$ErrorActionPreference='Stop'\n" + "\n".join(line + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE }" for line in lines)
        subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", script], cwd=cwd, env=environment, check=True)
    else:
        subprocess.run(["sh", "-eu", "-c", "\n".join(lines)], cwd=cwd, env=environment, check=True)


def collect(root, directory, paths, out):
    files = []
    for value in paths:
        source = path_in(directory, value)
        if not source.exists():
            raise ValueError(f"declared output is missing: {source}")
        for file in sorted(source.rglob("*")) if source.is_dir() else [source]:
            if file.is_symlink():
                raise ValueError("artifact symlinks are not supported")
            if not file.is_file():
                continue
            relative = file.relative_to(root)
            target = out / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
            files.append({"path": relative.as_posix(), "artifact": target.relative_to(root).as_posix(),
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    return files


def materialize(root, records):
    written = {}
    for record in records:
        for file in record.get("files", []):
            if file["path"] in written and written[file["path"]] != file["sha256"]:
                raise ValueError(f"upstream output collision: {file['path']}")
            written[file["path"]] = file["sha256"]
            target = path_in(root, file["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            source = path_in(root, file["artifact"])
            if source != target:
                shutil.copyfile(source, target)


def version(directory, settings, *, check_bump=False):
    path = path_in(directory, settings["version_file"])
    metadata = tomllib.loads(path.read_text())["project"]
    if "version" in metadata.get("dynamic", []) or "version" not in metadata:
        raise ValueError("static project.version required; dynamic version strategy is not supported")
    current = Version(metadata["version"])
    if current.local:
        raise ValueError("release version cannot have a local suffix")
    tag = os.environ.get("CI_COMMIT_TAG", "")
    if tag:
        owns_tag = bool(re.fullmatch(re.escape(settings["tag"]).replace(re.escape("{version}"), ".+"), tag))
        if (owns_tag or not check_bump) and settings["tag"].format(version=current) != tag:
            raise ValueError(f"tag must match {settings['tag'].format(version=current)}")
    elif check_bump and settings["bump"] and os.environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"):
        target = os.environ["CI_MERGE_REQUEST_TARGET_BRANCH_NAME"]
        subprocess.run(["git", "fetch", "--no-tags", "origin", f"+refs/heads/{target}:refs/ci/version-base"], check=True)
        root = Path(os.environ["CI_PROJECT_DIR"]).resolve()
        previous = subprocess.run(["git", "show", f"refs/ci/version-base:{path.relative_to(root).as_posix()}"], capture_output=True, text=True)
        if previous.returncode:
            raise ValueError("cannot read baseline version; initialize a new package explicitly before release validation")
        if current <= Version(tomllib.loads(previous.stdout)["project"]["version"]):
            raise ValueError("bump project.version above target branch")
    return str(current)


def bundle_dependencies(environment, directory, out, hosts):
    """Build an offline, hash-locked wheelhouse from the actual checked environment."""
    name = tomllib.loads((directory / "pyproject.toml").read_text())["project"]["name"]
    code = '''import importlib.metadata as m,json,sys
rows=[]
for d in m.distributions():
 if d.metadata['Name'].lower().replace('_','-') == sys.argv[1].lower().replace('_','-'): continue
 source=json.loads(d.read_text('direct_url.json') or '{}')
 url=source.get('url')
 if source.get('vcs_info'): url='git+'+url+'@'+source['vcs_info']['commit_id']
 if source.get('subdirectory') and url:
  from urllib.parse import quote
  url += '#subdirectory='+quote(source['subdirectory'], safe='/')
 rows.append(d.metadata['Name']+' @ '+url if url else d.metadata['Name']+'=='+d.version)
print('\\n'.join(rows))'''
    requirements = subprocess.check_output([environment["interpreter"], "-c", code, name], text=True)
    request = out / "resolved-requirements.txt"
    request.write_text(requirements)
    wheels = out / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-r", str(request), "--wheel-dir", str(wheels)], check=True)
    from packaging.utils import parse_wheel_filename
    locked = []
    for wheel in sorted(wheels.glob("*.whl")):
        package, ver, _, _ = parse_wheel_filename(wheel.name)
        locked.append(f"{package}=={ver} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}")
    (out / "requirements.txt").write_text("\n".join(locked) + "\n")
    write_json(out / "provenance.json", {k: v for k, v in environment.items() if k != "interpreter"})


def build_container(data, project_name, project, settings, root, directory, out, plan, records):
    infra = data["platform"]
    candidates = [c for c in plan["candidates"] if project_name in c["projects"]]
    if candidates and not settings["dependency_bundle"]:
        raise ValueError("candidate image requires container.dependency-bundle and the documented Dockerfile recipe")
    is_preview = bool(os.environ.get("CI_MERGE_REQUEST_IID"))
    protected = os.environ.get("CI_COMMIT_REF_PROTECTED") == "true"
    same_project = os.environ.get("CI_MERGE_REQUEST_SOURCE_PROJECT_ID") == os.environ.get("CI_PROJECT_ID")
    push = protected or (is_preview and same_project)
    if candidates and not is_preview:
        push = False
    repository = settings["repository"] or infra["registry"].rstrip("/") + "/" + project_name
    if is_preview:
        repository = infra["preview_registry"].rstrip("/") + "/" + project_name
    reference = repository + ":" + os.environ.get("CI_COMMIT_SHA", "local") + "-" + os.environ.get("CI_PIPELINE_ID", "local")
    context = settings["context"]
    context_path = path_in(root, context["repository"]) if isinstance(context, dict) else path_in(directory, context)
    dockerfile = path_in(directory, settings["dockerfile"])
    args = ["buildctl-daemonless.sh", "build", "--frontend", "dockerfile.v0", "--local", f"context={context_path}",
            "--local", f"dockerfile={dockerfile.parent}", "--opt", f"filename={dockerfile.name}", "--opt", f"platform={settings['platform']}",
            "--metadata-file", str(out / "image.json")]
    for name, value in settings["build_args"].items():
        args += ["--opt", f"build-arg:{name}={os.path.expandvars(value)}"]
    if settings["target"]:
        args += ["--opt", f"target={settings['target']}"]
    for name, variable in settings["secrets"].items():
        secret = os.environ.get(variable)
        if not secret or not Path(secret).is_file():
            raise ValueError(f"missing BuildKit secret file variable {variable}")
        args += ["--secret", f"id={name},src={secret}"]
    if settings["dependency_bundle"]:
        bundle = out / "dependencies"
        bundle.mkdir()
        with prepared(directory, root, project["dependencies"], plan["candidates"], project_name, infra["allowed_hosts"], bundle) as environment:
            if environment["manager"] != "uv":
                raise ValueError("dependency-bundle requires uv")
            # Check canonical source checks tested this exact universal lock.
            for record in records:
                previous = record.get("dependencies", {})
                previous_policy = {k: v for k, v in previous.get("policy", {}).items() if k != "python"}
                canonical_policy = {k: v for k, v in project["dependencies"].items() if k != "python"}
                if previous_policy == canonical_policy and previous.get("lock_sha256") != environment["lock_sha256"]:
                    raise ValueError("container dependency resolution differs from required check; commit upgraded lock before building")
            bundle_dependencies(environment, directory, bundle, infra["allowed_hosts"])
        args += ["--local", f"ci-dependencies={bundle}", "--opt", "context:ci-dependencies=local:ci-dependencies"]
    args += ["--output", f"type=image,name={reference},push=true" if push else f"type=oci,dest={out / 'image.oci.tar'}"]
    with tempfile.TemporaryDirectory() as auth:
        variable = "PREVIEW_REGISTRY_AUTH_FILE" if is_preview else "REGISTRY_AUTH_FILE"
        if push and not os.environ.get(variable):
            raise ValueError(f"set {variable} to an internal registry auth file")
        if os.environ.get(variable):
            shutil.copyfile(os.environ[variable], Path(auth) / "config.json")
        flags = os.environ.get("BUILDKITD_FLAGS", "--oci-worker-no-process-sandbox")
        if os.environ.get("BUILDKIT_CONFIG_FILE"):
            flags += " --config " + os.environ["BUILDKIT_CONFIG_FILE"]
        subprocess.run(args, cwd=root, env={**os.environ, "DOCKER_CONFIG": auth, "BUILDKITD_FLAGS": flags}, check=True)
    image_digest = json.loads((out / "image.json").read_text()).get("containerimage.digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_digest):
        raise ValueError("BuildKit did not return an image digest")
    return {"image": {"repository": repository, "digest": image_digest, "pushed": push}, "candidates": candidates}


def set_value(values, path, value):
    for part in path[:-1]:
        if not part or not isinstance(values, dict):
            raise ValueError("invalid chart image binding")
        values = values.setdefault(part, {})
    if path[-1] in values:
        raise ValueError("overlapping chart image bindings")
    values[path[-1]] = value


def deploy(data, node, root, directory, out, plan, records, *, stop=False):
    settings, infra = node["settings"], data["platform"]
    target = infra["targets"][settings["target"]]
    preview = settings["preview"]
    if target["production"] and (os.environ.get("CI_COMMIT_REF_PROTECTED") != "true" or plan.get("candidates")):
        raise ValueError("production requires a protected ref and an ordinary dependency run")
    suffix = f"-mr-{os.environ.get('CI_PROJECT_ID', '')}-{os.environ.get('CI_MERGE_REQUEST_IID', '')}" if preview else ""
    release_name = target["release_prefix"] + "-" + node["project"] + "-" + settings["name"] + suffix
    if len(release_name) > 53:
        release_name = release_name[:40].rstrip("-") + "-" + hashlib.sha256(release_name.encode()).hexdigest()[:12]
    namespace = os.path.expandvars(target["namespace"])
    environment = {"KUBECONFIG": os.environ[target["kubeconfig_variable"]], "TOOLKIT_DEPLOY_URL": os.path.expandvars(target["url"])}
    if stop:
        subprocess.run(["helm", "uninstall", release_name, "--namespace", namespace, "--ignore-not-found", "--wait"], env={**os.environ, **environment}, check=True)
        return {"release": release_name, "namespace": namespace, "cleanup": "chart-owned resources only"}
    images = {data["nodes"][job]["project"]: record["image"] for job, record in zip(node["needs"], records) if "image" in record}
    overlay = {}
    for project, binding in settings["images"].items():
        if project not in images or not images[project]["pushed"]:
            raise ValueError(f"no pushed image for {project}; deploy from a protected branch/tag or same-project MR")
        set_value(overlay, binding["repository"], images[project]["repository"])
        set_value(overlay, binding["digest"], images[project]["digest"])
    write_json(out / "image-values.json", overlay)
    chart = settings["chart"] or infra["chart"]
    if not chart.startswith("oci://"):
        chart = str(path_in(directory if settings["chart"] else root, chart))
    args = [release_name, chart, "--namespace", namespace]
    if chart.startswith("oci://"):
        args += ["--version", settings["chart_version"] or infra["chart_version"]]
    for file in settings["values"]:
        args += ["--values", str(path_in(directory, file))]
    args += ["--values", str(out / "image-values.json")]
    # Render and ensure each promised image binding actually appears in the workload manifest.
    rendered = subprocess.check_output(["helm", "template", *args], env={**os.environ, **environment}, text=True)
    workload_images = set()
    def scan(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"containers", "initContainers"} and isinstance(child, list):
                    workload_images.update(c.get("image") for c in child if isinstance(c, dict))
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)
    documents = list(yaml.safe_load_all(rendered))
    for document in documents:
        scan(document)
    for image in images.values():
        if image["repository"] + "@" + image["digest"] not in workload_images:
            raise ValueError("custom chart did not consume a required digest-pinned image binding")
    for document in documents:
        if isinstance(document, dict) and document.get("kind") == "Secret":
            for key in ("data", "stringData"):
                if key in document:
                    document[key] = {name: "REDACTED" for name in document[key]}
    (out / "rendered.yaml").write_text(yaml.safe_dump_all(documents))
    commands(settings["before"], directory, node["shell"], environment)
    major = subprocess.check_output(["helm", "version", "--template", "{{.Version}}"], text=True)
    flag = "--atomic" if major.startswith("v3.") else "--rollback-on-failure" if major.startswith("v4.") else None
    if not flag:
        raise ValueError("unsupported Helm version")
    subprocess.run(["helm", "upgrade", "--install", *args, "--create-namespace", "--wait", "--timeout", "10m", flag], env={**os.environ, **environment}, check=True)
    # Explicitly not a database rollback: after-check failure is surfaced to GitLab.
    commands(settings["after"], directory, node["shell"], environment)
    return {"release": release_name, "namespace": namespace, "images": images}


def publish(data, node, directory, root, out, plan, records):
    if plan["candidates"] or os.environ.get("CI_COMMIT_REF_PROTECTED") != "true" or not os.environ.get("CI_COMMIT_TAG"):
        raise ValueError("publication requires a protected tag without candidate overrides")
    settings = node["settings"]
    current = version(directory, settings)
    project = data["pipeline"]["projects"][node["project"]]
    result = {"version": current, "tag": os.environ["CI_COMMIT_TAG"], "publications": []}
    package = project["package"]
    if package is not None:
        if not package["index"]:
            raise ValueError("package.index must select a named tool.uv.index for publication")
        package_directory = path_in(directory, package["directory"])
        metadata = tomllib.loads((package_directory / "pyproject.toml").read_text())
        indexes = [r for r in metadata.get("tool", {}).get("uv", {}).get("index", []) if r.get("name") == package["index"]]
        if len(indexes) != 1 or not indexes[0].get("publish-url"):
            raise ValueError("selected uv index must define exactly one publish-url")
        for key in ("url", "publish-url"):
            allowed_url(indexes[0][key], data["platform"]["allowed_hosts"])
        package_records = [r for r in records if r.get("action") == "package" and r["project"] == node["project"]]
        if len(package_records) != 1 or package_records[0]["package"]["version"] != current:
            raise ValueError("built package version does not match release version")
        files = [path_in(root, f["artifact"]) for r in package_records for f in r.get("files", []) if f["path"].endswith((".whl", ".tar.gz"))]
        if not any(p.suffix == ".whl" for p in files) or not any(p.name.endswith(".tar.gz") for p in files):
            raise ValueError("release requires built wheel and sdist artifacts")
        subprocess.run(["uv", "publish", "--index", package["index"], *map(str, files)], cwd=package_directory, check=True)
        result["publications"].append({"index": package["index"], "files": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]})
        write_json(out / "publication.json", result)
    if settings["gitlab_release"]:
        subprocess.run(["glab", "release", "create", os.environ["CI_COMMIT_TAG"], "--notes-file", str(path_in(directory, settings["notes"]))], cwd=root, check=True)
    return result


def run_job(data, identifier, root, expected):
    node = data["nodes"][identifier]
    out = root / ".ci-out" / identifier
    out.mkdir(parents=True, exist_ok=True)
    # Never retain a success marker from an earlier retry, including shell executors.
    receipt_path(root, identifier).unlink(missing_ok=True)
    if node["action"] == "stop":
        return deploy(data, node, root, root, out, {}, [], stop=True)
    plan = json.loads((root / ".ci-out/plan.json").read_text())
    if any(plan.get(k) != v for k, v in identity(expected).items()):
        raise ValueError("pipeline plan is stale")
    project = data["pipeline"]["projects"][node["project"]]
    if node["project"] not in plan["selected"]:
        print("Project unaffected; no success evidence emitted")
        return
    records = [require_receipt(root, job, expected) for job in node["needs"]]
    materialize(root, records)
    directory = path_in(root, project["path"])
    action, settings = node["action"], node["settings"]
    result = {**identity(expected), "status": "passed", "project": node["project"], "action": action, "created": time.time(),
              "candidates": plan["candidates"], "files": []}
    if action in {"check", "step"}:
        policy = settings.get("dependencies") or project["dependencies"]
        # Matrix PYTHON_VERSION is a public convention and is checked, not just used in an image tag.
        policy = {**policy}
        if "PYTHON_VERSION" in node["variables"]:
            policy["python"] = node["variables"]["PYTHON_VERSION"]
        with prepared(directory, root, policy, plan["candidates"], node["project"], data["platform"]["allowed_hosts"], out) as environment:
            result["dependencies"] = {k: v for k, v in environment.items() if k != "interpreter"}
            image_env = {"TOOLKIT_IMAGE_" + r["project"].upper().replace("-", "_"): r["image"]["repository"] + "@" + r["image"]["digest"] for r in records if "image" in r}
            try:
                commands(settings["script"], directory, node["shell"], image_env)
            finally:
                reports = out / "reports"
                reports.mkdir(exist_ok=True)
                for index, pattern in enumerate(settings.get("junit", [])):
                    for j, file in enumerate(directory.glob(pattern)):
                        shutil.copyfile(file, reports / f"{index}-{j}.xml")
            result["files"] = collect(root, directory, settings.get("outputs", {}).values(), out)
    elif action == "package":
        package_directory = path_in(directory, settings["directory"])
        from .dependencies import validate_indexes
        metadata = validate_indexes(package_directory, data["platform"]["allowed_hosts"])["project"]
        if project["release"]:
            version(directory, project["release"], check_bump=True)
        dist = out / "dist"
        subprocess.run(["uv", "build", "--no-sources", "--out-dir", str(dist)], cwd=package_directory, check=True)
        files = sorted(dist.glob("*"))
        from packaging.utils import parse_wheel_filename, parse_sdist_filename
        for file in files:
            if file.name.endswith(".whl"):
                _, built_version, _, _ = parse_wheel_filename(file.name)
            elif file.name.endswith(".tar.gz"):
                _, built_version = parse_sdist_filename(file.name)
            else:
                raise ValueError(f"unexpected package build artifact {file.name}")
            if built_version != Version(metadata["version"]):
                raise ValueError("built distribution version does not match pyproject")
        result["package"] = {"name": metadata["name"], "version": str(Version(metadata["version"]))}
        subprocess.run([sys.executable, "-m", "twine", "check", *map(str, files)], check=True)
        # Install the built wheel in an isolated environment; source-tree tests alone are insufficient.
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["uv", "venv", "--no-python-downloads", tmp], check=True)
            interpreter = Path(tmp) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(["uv", "pip", "install", "--python", str(interpreter), *map(str, dist.glob("*.whl"))], check=True)
            subprocess.run(["uv", "pip", "check", "--python", str(interpreter)], check=True)
        result["files"] = [{"path": f.relative_to(root).as_posix(), "artifact": f.relative_to(root).as_posix(), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()} for f in files]
    elif action == "container":
        if project["release"]:
            version(directory, project["release"], check_bump=True)
        result.update(build_container(data, node["project"], project, settings, root, directory, out, plan, records))
    elif action == "deploy":
        with prepared(directory, root, project["dependencies"], plan["candidates"], node["project"], data["platform"]["allowed_hosts"], out) as environment:
            result["dependencies"] = {k: v for k, v in environment.items() if k != "interpreter"}
            result.update(deploy(data, node, root, directory, out, plan, records))
    elif action == "release":
        result.update(publish(data, node, directory, root, out, plan, records))
    else:
        raise ValueError(f"unsupported action {action}")
    write_json(receipt_path(root, identifier), result)


def main():
    try:
        command = sys.argv[1]
        expected = sys.argv[-1]
        data = load_config(expected)
        root = Path(os.environ.get("CI_PROJECT_DIR", ".")).resolve()
        if command == "plan":
            make_plan(data, root, expected)
        elif command == "run":
            run_job(data, sys.argv[2], root, expected)
        else:
            raise ValueError("expected plan or run")
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"generic-ci: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
