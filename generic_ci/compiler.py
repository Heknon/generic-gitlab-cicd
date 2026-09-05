"""Deterministic compilation to a committed, top-level GitLab pipeline."""
import base64
import hashlib
import itertools
import json
import re
from pathlib import Path

import yaml

from . import __version__


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def variants(native):
    matrix = native.get("parallel")
    if not matrix:
        return [{}]
    if set(matrix) != {"matrix"} or not isinstance(matrix["matrix"], list):
        raise ValueError("gitlab.parallel must contain a native matrix list")
    result = []
    for row in matrix["matrix"]:
        if not isinstance(row, dict) or not row:
            raise ValueError("matrix rows must be nonempty mappings")
        keys = list(row)
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or k.startswith(("CI_", "TOOLKIT_")) for k in keys):
            raise ValueError("matrix keys must be uppercase and cannot use CI_ or TOOLKIT_ prefixes")
        choices = [v if isinstance(v, list) else [v] for v in row.values()]
        if any(not c or any(not isinstance(v, (str, int)) or isinstance(v, bool) for v in c) for c in choices):
            raise ValueError("matrix values must be strings or integers (quote Python versions)")
        for values in itertools.product(*choices):
            result.append(dict(zip(keys, map(str, values))))
            if len(result) > 200:
                raise ValueError("matrix exceeds 200 combinations")
    if len({json.dumps(v, sort_keys=True) for v in result}) != len(result):
        raise ValueError("duplicate matrix combinations")
    return result


def compile_pipeline(pipeline, platform, *, sources=None):
    data = pipeline.model_dump()
    infra = platform.model_dump()
    nodes = {}
    refs = {}

    def add(identifier, project, action, settings, *, native=None, variables=None):
        if identifier in nodes:
            raise ValueError(f"duplicate generated job {identifier}")
        nodes[identifier] = {"project": project, "action": action, "settings": settings,
                             "native": native or {}, "variables": variables or {}, "needs": []}
        return identifier

    def check_refs(project, names):
        p = data["projects"][project]
        selected = names if names is not None else [k for k, v in p["checks"].items() if v]
        for required in infra["mandatory_checks"]:
            if required not in selected:
                raise ValueError(f"{project}: missing mandatory check {required}")
        return [f"{project}.{name}" for name in selected]

    for name, project in data["projects"].items():
        for category in ("steps", "checks"):
            for key, settings in project[category].items():
                if settings is False:
                    continue
                public = f"{name}.{key}"
                if public in refs or key in {"container", "package", "release"}:
                    raise ValueError(f"{public}: check/step name collides with a public output")
                rows = variants(settings["gitlab"])
                if category == "steps" and settings["outputs"] and len(rows) > 1:
                    raise ValueError(f"{public}: output-producing steps must have one variant; use separate named steps")
                refs[public] = []
                for index, variables in enumerate(rows):
                    identifier = f"{name}-{key}" + (f"-{index + 1}" if len(rows) > 1 else "")
                    refs[public].append(add(identifier, name, "step" if category == "steps" else "check", settings,
                                            native=settings["gitlab"], variables=variables))
        if project["package"] is not None:
            refs[f"{name}.package"] = [add(f"{name}-package", name, "package", project["package"])]
        if project["container"] is not None:
            refs[f"{name}.container"] = [add(f"{name}-container", name, "container", project["container"])]
        for key, deployment in project["deploy"].items():
            add(f"{name}-deploy-{key}", name, "deploy", {**deployment, "name": key})
            if deployment["preview"]:
                add(f"{name}-stop-{key}", name, "stop", {**deployment, "name": key})
        if project["release"]:
            refs[f"{name}.release"] = [add(f"{name}-release", name, "release", project["release"])]

    for identifier, node in nodes.items():
        name = node["project"]
        project = data["projects"][name]
        settings = node["settings"]
        requested = []
        if node["action"] in {"step", "check", "container", "package"}:
            requested += [r if "." in r else f"{name}.{r}" for r in settings.get("needs", [])]
        if node["action"] in {"container", "package"}:
            requested += check_refs(name, settings.get("checks"))
        if node["action"] == "deploy":
            requested += check_refs(name, settings["checks"])
            images = settings["images"] or {name: {"repository": ["apps", name, "image", "repository"],
                                                  "digest": ["apps", name, "image", "digest"]}}
            settings["images"] = images
            requested += [f"{p}.container" for p in images]
        if node["action"] == "release":
            requested += [f"{p}.release" for p in settings["needs"]]
            requested += [f"{name}.{kind}" for kind in ("package", "container") if project[kind] is not None]
            requested += check_refs(name, None)
        for ref in dict.fromkeys(requested):
            if ref not in refs:
                raise ValueError(f"{identifier}.needs: unknown/disabled reference {ref}")
            for upstream in refs[ref]:
                if nodes[upstream]["native"].get("allow_failure"):
                    raise ValueError(f"{identifier}: required check {ref} cannot allow failure")
                node["needs"].append(upstream)
        node["needs"] = sorted(set(node["needs"]))
        if len(node["needs"]) >= 50:
            raise ValueError(f"{identifier}: too many dependencies for supported GitLab needs limit")

    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError(f"dependency cycle involving {key}")
        if key in visited:
            return
        visiting.add(key)
        for upstream in nodes[key]["needs"]:
            visit(upstream)
        visiting.remove(key)
        visited.add(key)

    for key in nodes:
        visit(key)
    # Selection follows actual cross-project graph edges as well as declared source dependencies.
    for node in nodes.values():
        if node["action"] == "release":
            continue
        project = data["projects"][node["project"]]
        project["depends_on"] = sorted(set(project["depends_on"]) | {
            nodes[k]["project"] for k in node["needs"] if nodes[k]["project"] != node["project"]})
    if len(nodes) + 1 > platform.max_jobs:
        raise ValueError(f"pipeline creates {len(nodes) + 1} jobs; platform limit is {platform.max_jobs}")
    payload = {"version": __version__, "pipeline": data, "platform": infra, "nodes": nodes, "sources": sources or {}}
    fingerprint = digest(payload)
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    common_rules = [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}, {"if": "$CI_COMMIT_TAG"}, {"if": "$CI_COMMIT_BRANCH"}]
    preview_rules = [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event" && $CI_MERGE_REQUEST_SOURCE_PROJECT_ID == $CI_PROJECT_ID'}]
    protected_rules = [{"if": '$CI_COMMIT_REF_PROTECTED == "true" && $CI_COMMIT_TAG'}]
    default = infra["runtimes"]["default"]
    output = {
        "stages": ["prepare", "delivery"],
        "workflow": {"auto_cancel": {"on_new_commit": "conservative"}, "rules": [
            {"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}, {"if": "$CI_COMMIT_TAG"},
            {"if": '$CI_PIPELINE_SOURCE == "push" && $CI_OPEN_MERGE_REQUESTS', "when": "never"},
            {"if": "$CI_COMMIT_BRANCH"}, {"when": "never"}]},
        "variables": {
            "TOOLKIT_CONFIG_B64": encoded,
            "CI_DEPENDENCY_OVERRIDES": {"value": "[]", "description": "Temporary JSON array of package/repository/ref/subdirectory overrides"},
            "CI_DEPENDENCY_FILE": {"value": "", "description": "Optional override JSON file; exclusive with other override inputs"},
            "CI_DEPENDENCY_REPO": {"value": "", "description": "Single candidate repository HTTPS URL"},
            "CI_DEPENDENCY_REF": {"value": "", "description": "Single candidate branch, tag or SHA"},
            "CI_DEPENDENCY_PACKAGE": {"value": "", "description": "Single candidate distribution name"},
            "CI_DEPENDENCY_SUBDIRECTORY": {"value": "", "description": "Package path within candidate repository"},
            "CI_FULL_PIPELINE": {"value": "false", "options": ["false", "true"], "description": "Run all projects regardless of changed paths"},
        },
        "toolkit-plan": {"stage": "prepare", "tags": default["tags"], "script": [f"python -m generic_ci.runtime plan {fingerprint}"],
                         "rules": common_rules, "artifacts": {"paths": [".ci-out/plan.json"], "expire_in": infra["artifact_retention"]},
                         "interruptible": True},
    }
    if default["image"]:
        output["toolkit-plan"]["image"] = default["image"]
    for identifier, node in nodes.items():
        action = node["action"]
        runtime_key = {"container": "build", "deploy": "helm", "stop": "helm", "release": "release"}.get(action, "default")
        if action in {"check", "step"}:
            runtime_key = node["settings"]["runtime"]
            if runtime_key not in infra["runtimes"]:
                raise ValueError(f"{identifier}.runtime: unknown runtime {runtime_key}")
        runtime = infra["runtimes"].get(runtime_key, default)
        native = node["native"]
        node["shell"] = runtime["shell"]  # Set before final fingerprint below.
        job = {"stage": "delivery", "tags": runtime["tags"], "interruptible": action in {"check", "step", "package"},
               "script": [f"python -m generic_ci.runtime run {identifier} FINGERPRINT"],
               "needs": [{"job": "toolkit-plan", "artifacts": True}] + [{"job": k, "artifacts": True, "optional": True} for k in node["needs"]],
               "rules": common_rules, "allow_failure": native.get("allow_failure", False), "retry": 0,
               "variables": {**native.get("variables", {}), **node["variables"]},
               "artifacts": {"when": "always", "expire_in": infra["artifact_retention"], "paths": [f".ci-out/{identifier}/"]}}
        if runtime["image"]:
            job["image"] = runtime["image"]
        for key in ("image", "tags", "services", "timeout", "rules", "cache", "resource_group"):
            if native.get(key) is not None:
                job[key] = native[key]
        if node["settings"].get("junit"):
            job["artifacts"]["reports"] = {"junit": [f".ci-out/{identifier}/reports/*.xml"]}
        if action in {"deploy", "stop"}:
            settings = node["settings"]
            target = infra["targets"][settings["target"]]
            preview = settings["preview"]
            env = f"review/{node['project']}/{settings['name']}/$CI_MERGE_REQUEST_IID" if preview else f"{node['project']}/{settings['target']}"
            job["environment"] = {"name": env, "url": target["url"], "deployment_tier": "development" if preview else ("production" if target["production"] else "staging")}
            job["resource_group"] = env
            job["rules"] = preview_rules if preview else (protected_rules if target["production"] else common_rules)
            job["when"] = "manual" if action == "stop" else settings["when"]
            if preview:
                job["environment"].update({"on_stop": identifier.replace("-deploy-", "-stop-"), "auto_stop_in": settings["auto_stop_in"]})
            if action == "stop":
                job["environment"].pop("on_stop", None)
                job["environment"].pop("auto_stop_in", None)
                job["environment"]["action"] = "stop"
                job["variables"]["GIT_STRATEGY"] = "none"
                job["needs"] = []
                job["allow_failure"] = True
        if action == "release":
            tag = node["settings"]["tag"]
            pattern = re.escape(tag).replace(re.escape("{version}"), ".+").replace("/", "\\/")
            job["rules"] = [{"if": f'$CI_COMMIT_REF_PROTECTED == "true" && $CI_COMMIT_TAG =~ /^{pattern}$/'}]
            job["when"] = "manual"
            job["resource_group"] = f"release/{node['project']}"
            job["environment"] = {"name": f"publish/{node['project']}", "deployment_tier": "other"}
        output[identifier] = job
    # Shell metadata is also signed by the expected configuration fingerprint.
    fingerprint = digest(payload)
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    if len(encoded) > 100_000:
        raise ValueError("compiled execution configuration exceeds 100 KB; split the pipeline into smaller project groups")
    output["variables"]["TOOLKIT_CONFIG_B64"] = encoded
    output["toolkit-plan"]["script"] = [f"python -m generic_ci.runtime plan {fingerprint}"]
    for identifier in nodes:
        output[identifier]["script"] = [f"python -m generic_ci.runtime run {identifier} {fingerprint}"]
    return output, payload


def render(pipeline, platform, *, sources=None):
    jobs, _ = compile_pipeline(pipeline, platform, sources=sources)
    return "# Generated by generic-ci; edit the project configuration and render again.\n" + yaml.safe_dump(jobs, sort_keys=False, width=120)


def source_hashes(root, paths):
    root = Path(root).resolve()
    return {Path(path).resolve().relative_to(root).as_posix(): hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}
