"""Offline authoring CLI: schema, validate, explain, render and drift checking."""
import argparse
import json
from pathlib import Path
import sys

from pydantic import ValidationError

from .compiler import compile_pipeline, render, source_hashes
from .config import load
from .models import Pipeline, Platform


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'source':
        from .sources import source_main
        try:
            return source_main(argv[1:])
        except (ValueError, OSError, ValidationError) as error:
            print(f'generic-ci: {error}', file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(prog="generic-ci")
    parser.add_argument("command", choices=["schema", "validate", "explain", "render", "init"])
    parser.add_argument("--config")
    parser.add_argument("--format", choices=["workflows", "legacy"], default="workflows")
    parser.add_argument("--ecosystem", choices=["python", "npm", "pnpm", "bun"], default="python")
    parser.add_argument("--platform")
    parser.add_argument("--template")
    parser.add_argument("--source")
    parser.add_argument("--repo")
    parser.add_argument("--ref")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", "-o")
    parser.add_argument("--check", action="store_true", help="Fail if output differs; never modify output")
    parser.add_argument("--platform-schema", action="store_true")
    options = parser.parse_args(argv)
    try:
        root = Path(options.root).resolve()
        use_source = options.format == 'workflows' and (root / 'generic-ci.yml').exists()
        from .sources import home
        default_source = (home() / 'sources.json').exists()
        if options.command == 'init' and (options.template or options.source or options.repo or default_source):
            from .sources import initialize
            initialize(root, options.template, options.source, options.repo, options.ref, options.offline, options.config)
            return 0
        config_path = root / (options.config or 'delivery.yml')
        platform_path = root / (options.platform or 'ci-platform.yml')
        if options.format == "workflows":
            from .workflows import compiler as workflow_compiler
            from .workflows.models import Pipeline as WorkflowPipeline, Platform as WorkflowPlatform
        if options.command == "init":
            import yaml
            if config_path.exists():
                raise ValueError("configuration exists; init will not overwrite it")
            ecosystem = {"python": {}} if options.ecosystem == "python" else {"node": {"package-manager": options.ecosystem}}
            command = "uv run --no-sync pytest" if options.ecosystem == "python" else options.ecosystem + " run test"
            example = {"version": 1, "projects": {"app": {"path": ".", **ecosystem,
                "checks": {"unit": {"script": [command]}},
                "workflows": {"push": {"checks": ["unit"]}, "merge-request": {"checks": ["unit"]}}}}}
            config_path.write_text(yaml.safe_dump(example, sort_keys=False))
            print(f"Created {config_path}; provide an internal platform configuration with --platform")
            return 0
        if options.command == "schema":
            model = (WorkflowPlatform if options.platform_schema else WorkflowPipeline) if options.format == "workflows" else (Platform if options.platform_schema else Pipeline)
            result = json.dumps(model.model_json_schema(by_alias=True), indent=2) + "\n"
        else:
            if options.format == "workflows":
                if use_source:
                    from .sources import load_project
                    pipeline, platform, origins, inputs = load_project(root, options.config, options.platform, options.offline)
                else:
                    pipeline, platform = workflow_compiler.load(config_path, platform_path)
                    origins = {"checks": "developer-defined; no hidden suites"}
                    inputs = [config_path, platform_path]
            else:
                pipeline, platform, origins = load(config_path, platform_path)
                inputs = [config_path, platform_path]
            sources = source_hashes(root, inputs)
            jobs, payload = (workflow_compiler.compile_pipeline(pipeline, platform, sources=sources) if options.format == "workflows" else compile_pipeline(pipeline, platform, sources=sources))
            if options.command == "validate":
                result = f"Valid: {len(pipeline.projects)} projects; {len(payload['nodes']) + 1} jobs. Target GitLab CI Lint is still required.\n"
            elif options.command == "explain":
                result = json.dumps({"generation": "committed top-level GitLab CI", "origins": origins,
                                     "projects": payload["pipeline"]["projects"], "platform": payload["platform"], "jobs": payload["nodes"]}, indent=2) + "\n"
            else:
                if options.format == "workflows":
                    import yaml
                    result = "# Generated by generic-ci; edit delivery configuration and render again.\n" + yaml.safe_dump(jobs, sort_keys=False)
                else:
                    result = render(pipeline, platform, sources=sources)
        if options.check:
            if not options.output or not Path(options.output).is_file() or Path(options.output).read_text() != result:
                raise ValueError("generated output differs; render again and commit it")
            return 0
        if options.output:
            target = Path(options.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result)
        else:
            print(result, end="")
        return 0
    except (ValueError, OSError, ValidationError) as error:
        print(f"generic-ci: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
