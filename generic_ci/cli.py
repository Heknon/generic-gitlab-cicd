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
    if argv and argv[0] == 'setup':
        from .setup import setup_main
        try:
            return setup_main(argv[1:])
        except (ValueError, OSError, EOFError, ValidationError) as error:
            print(f'generic-ci setup: {error}', file=sys.stderr)
            return 1
    if argv and argv[0] == 'source':
        from .sources import source_main
        try:
            return source_main(argv[1:])
        except (ValueError, OSError, ValidationError, KeyError) as error:
            print(f'generic-ci: {error}', file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(prog="generic-ci", description="Author, inspect and validate GitLab delivery workflows.")
    parser.add_argument("--version", action="version", version=__import__('generic_ci').__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    common.add_argument("--config")
    common.add_argument("--platform")
    common.add_argument("--format", choices=["workflows", "legacy"], default="workflows")
    common.add_argument("--offline", action="store_true")
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--output", "-o")
    output.add_argument("--check", action="store_true", help="Compare without writing; nonzero on drift")
    descriptions = {"schema": "Export editor schemas", "validate": "Validate configuration and graph", "render": "Generate committed GitLab YAML",
                    "explain": "Explain commands, selection and delivery gates", "doctor": "Check local inputs and list infrastructure requirements",
                    "lint": "Validate using GitLab CI Lint without running jobs", "upgrade": "Preview or apply schemas, source and runtime binding updates",
                    "init": "Copy an organization template or built-in starter"}
    commands = {name: sub.add_parser(name, help=description, parents=[common, output]) for name, description in descriptions.items()}
    sub.add_parser("setup", help="Guided project setup (see setup --help)")
    sub.add_parser("source", help="Manage pinned organization sources (see source --help)")
    commands['schema'].add_argument("--platform-schema", action="store_true")
    init = commands['init']
    init.add_argument("--ecosystem", choices=["python", "npm", "pnpm", "bun"], default="python")
    for flag in ['template', 'source', 'repo', 'ref']:
        init.add_argument('--' + flag)
    explain = commands['explain']
    explain.add_argument("--event", choices=["push", "merge-request", "release", "manual", "schedule", "api", "trigger", "pipeline"])
    explain.add_argument("--changed", nargs="*", help="Changed repository paths; an empty list means no changes")
    explain.add_argument("--tag", default="", help="Release tag for simulation")
    for name in ['explain', 'doctor']:
        commands[name].add_argument("--json", action="store_true", help="Machine-readable output")
    commands['doctor'].add_argument("--runtime-role", choices=["control", "python", "node", "bun", "helm", "builder", "all"], help="Also check tools in this environment")
    commands['lint'].add_argument("--gitlab-url")
    commands['lint'].add_argument("--project", help="GitLab project ID or group/path")
    commands['lint'].add_argument("--ref")
    commands['lint'].add_argument("--simulate", action="store_true", help="Simulate pipeline creation in CI Lint; never run jobs")
    commands['upgrade'].add_argument("--apply", action="store_true", help="Apply the preview")
    commands['upgrade'].add_argument("--source-ref", help="Update the organization source revision")
    commands['upgrade'].add_argument("--runtime-image", action="append", default=[], help="ROLE=IMAGE; repeat per role")
    parser.set_defaults(event=None, changed=None, tag='', json=False, runtime_role=None, platform_schema=False,
                        template=None, source=None, repo=None, ref=None, apply=False, source_ref=None, runtime_image=[])
    options = parser.parse_args(argv)
    try:
        root = Path(options.root).resolve()
        if options.command == 'upgrade':
            from .authoring import upgrade
            if options.format != 'workflows':
                raise ValueError('upgrade supports workflow projects; migrate legacy configuration explicitly')
            if options.check and options.apply:
                raise ValueError('--check cannot be combined with --apply')
            changed = upgrade(root, options.output or root / '.gitlab-ci.yml', options.config, options.platform,
                              options.offline, options.source_ref, options.runtime_image, options.apply)
            return int(options.check and changed)
        if options.changed is not None and not options.event:
            raise ValueError('--changed requires --event')
        if options.event == 'release' and not options.tag:
            raise ValueError('release simulation requires --tag')
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
            status = 0
            if options.command in {'doctor', 'lint'}:
                if options.format != 'workflows':
                    raise ValueError('doctor/lint support workflow projects')
                from .authoring import doctor, lint
                if options.command == 'doctor':
                    report = doctor(payload, root, options.runtime_role)
                    result = json.dumps(report, indent=2) + '\n' if options.json else '\n'.join(
                        [r['status'].upper() + ' ' + r['check'] + ': ' + r['detail'] for r in report['checks']] +
                        ['Required CI variables: ' + ', '.join(report['requirements']['variables']),
                         'Not verified: ' + ', '.join(report['requirements']['not_verified'])]) + '\n'
                else:
                    if options.offline:
                        raise ValueError('GitLab lint requires a connection; omit --offline')
                    import yaml
                    report = lint(yaml.safe_dump(jobs, sort_keys=False), payload['platform'], options.gitlab_url, options.project, options.ref, options.simulate)
                    result = json.dumps(report, indent=2) + '\n'
                status = int(not report.get('valid', False))
            elif options.command == "validate":
                result = f"Valid: {len(pipeline.projects)} projects; {len(payload['nodes']) + 1} jobs. Target GitLab CI Lint is still required.\n"
            elif options.command == "explain":
                from .authoring import explain
                if options.format == 'workflows':
                    result = explain(payload, jobs, options.event, options.changed, options.tag, options.json, origins)
                else:
                    result = json.dumps({"origins": origins, "projects": payload["pipeline"]["projects"],
                                         "platform": payload["platform"], "jobs": payload["nodes"]}, indent=2) + "\n"
            else:
                if options.format == "workflows":
                    import yaml
                    result = "# Generated by generic-ci; edit delivery configuration and render again.\n" + yaml.safe_dump(jobs, sort_keys=False)
                else:
                    result = render(pipeline, platform, sources=sources)
        if options.command == "schema":
            status = 0
        if options.check:
            if not options.output or not Path(options.output).is_file() or Path(options.output).read_text() != result:
                raise ValueError("generated output differs; render again and commit it")
            return status
        if options.output:
            target = Path(options.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result)
        else:
            print(result, end="")
        return status
    except (ValueError, OSError, ValidationError, KeyError) as error:
        print(f"generic-ci: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
