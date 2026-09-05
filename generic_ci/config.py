"""Safe YAML loading, explicit preset expansion, and path/relationship validation."""
import copy
import fnmatch
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .models import Pipeline, Platform, relative


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError(f"YAML mapping keys must be strings at line {key_node.start_mark.line + 1}")
        if key in result:
            raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def read_yaml(path):
    text = Path(path).read_text()
    if len(text) > 2_000_000:
        raise ValueError("configuration exceeds 2 MB")
    try:
        return yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        where = f' at line {mark.line + 1}, column {mark.column + 1}' if mark else ''
        raise ValueError(f'{path}: invalid YAML{where}: {getattr(error, "problem", "parse error")}') from None


def allowed_url(value, hosts, *, registry=False):
    parsed = urlsplit("https://" + value if registry else value.replace("oci://", "https://", 1))
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.query:
        raise ValueError(f"endpoint must use an allowed credential-free HTTPS host (host: {parsed.hostname})")


def load(config_path, platform_path):
    raw = read_yaml(config_path)
    # Validate before merging so unknown keys cannot disappear through expansion.
    declared = Pipeline.model_validate(raw)
    effective = copy.deepcopy(raw)
    origins = {}
    for name, project in declared.projects.items():
        row = effective["projects"][name]
        if project.preset.startswith("python-"):
            defaults = {"lint": {"script": ["uv run --no-sync ruff check ."]},
                        "unit": {"script": ["uv run --no-sync pytest --junitxml=reports/junit.xml"],
                                 "junit": ["reports/junit.xml"]}}
            explicit = row.get("checks", {})
            merged = {}
            for check, settings in defaults.items():
                override = explicit.get(check, {})
                merged[check] = False if override is False else {**settings, **override}
                origins[f"projects.{name}.checks.{check}"] = "project" if check in explicit else project.preset
            row["checks"] = {**merged, **{k: v for k, v in explicit.items() if k not in defaults}}
            row.setdefault("dependencies", {"manager": "uv"})
            if project.preset == "python-package":
                row.setdefault("package", {})
            else:
                row.setdefault("container", {})
        else:
            row.setdefault("dependencies", {"manager": "none"})
    pipeline = Pipeline.model_validate(effective)
    platform = Platform.model_validate(read_yaml(platform_path))
    for runtime in platform.runtimes.values():
        if runtime.image:
            allowed_url(runtime.image, platform.allowed_hosts, registry=True)
    for registry in (platform.registry, platform.preview_registry):
        allowed_url(registry, platform.allowed_hosts, registry=True)
    if platform.registry.rstrip("/") == platform.preview_registry.rstrip("/"):
        raise ValueError("preview-registry must be separate from registry")
    if platform.chart.startswith("oci://"):
        allowed_url(platform.chart, platform.allowed_hosts)
    else:
        relative(platform.chart)
    for name, project in pipeline.projects.items():
        for check_name, check in project.checks.items():
            if check and not check.script:
                raise ValueError(f"projects.{name}.checks.{check_name}.script: required for a custom check")
        for check in [*project.checks.values(), *project.steps.values()]:
            if check and check.gitlab.image:
                allowed_url(check.gitlab.image, platform.allowed_hosts, registry=True)
        for dependency in project.depends_on:
            if dependency not in pipeline.projects:
                raise ValueError(f"projects.{name}.depends-on: unknown project {dependency}")
        for mandatory in platform.mandatory_checks:
            if not project.checks.get(mandatory):
                raise ValueError(f"projects.{name}.checks.{mandatory}: platform-required check is missing/disabled")
        if project.container and project.container.repository:
            allowed_url(project.container.repository, platform.allowed_hosts, registry=True)
        for dep_name, deployment in project.deploy.items():
            prefix = f"projects.{name}.deploy.{dep_name}"
            if deployment.target not in platform.targets:
                raise ValueError(f"{prefix}.target: unknown target {deployment.target}")
            if deployment.preview and platform.targets[deployment.target].production:
                raise ValueError(f"{prefix}: previews require a nonproduction target")
            if deployment.chart:
                if deployment.chart.startswith("oci://"):
                    allowed_url(deployment.chart, platform.allowed_hosts)
                    if not deployment.chart_version:
                        raise ValueError(f"{prefix}: custom OCI chart requires chart-version")
                else:
                    relative(deployment.chart)
                if not deployment.images:
                    raise ValueError(f"{prefix}.images: custom chart requires explicit image bindings")
            checks = deployment.checks if deployment.checks is not None else [k for k, v in project.checks.items() if v]
            for check in checks:
                if not project.checks.get(check):
                    raise ValueError(f"{prefix}.checks: unknown or disabled check {check}")
            if not set(platform.mandatory_checks) <= set(checks):
                raise ValueError(f"{prefix}.checks: cannot remove platform-required checks")
            for source in deployment.images:
                if source not in pipeline.projects or not pipeline.projects[source].container:
                    raise ValueError(f"{prefix}.images: {source} is not a container project")
        if project.release:
            for dep in project.release.needs:
                if dep not in pipeline.projects or not pipeline.projects[dep].release:
                    raise ValueError(f"projects.{name}.release.needs: unknown release {dep}")
                if pipeline.projects[dep].release.tag != project.release.tag:
                    raise ValueError(f"projects.{name}.release.needs: same-pipeline release dependencies require a common tag convention")
    owners = [p.release.tag for p in pipeline.projects.values() if p.release and p.release.gitlab_release]
    if len(owners) != len(set(owners)):
        raise ValueError("a shared release tag must have exactly one gitlab-release owner")
    return pipeline, platform, origins


def affected(pipeline, changed):
    """None means unavailable baseline: full run. Empty means no source change."""
    if changed is None:
        return set(pipeline["projects"])
    selected = set()
    for name, project in pipeline["projects"].items():
        root = project["path"].rstrip("/")
        for path in changed:
            if root == "." or path == root or path.startswith(root + "/") or any(fnmatch.fnmatchcase(path, pat) for pat in project["watch"]):
                selected.add(name)
    while True:
        expanded = selected | {name for name, p in pipeline["projects"].items() if set(p["depends_on"]) & selected}
        if expanded == selected:
            return selected
        selected = expanded
