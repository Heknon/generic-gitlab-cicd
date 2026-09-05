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
    parser = argparse.ArgumentParser(prog="generic-ci")
    parser.add_argument("command", choices=["schema", "validate", "explain", "render"])
    parser.add_argument("--config", default="delivery.yml")
    parser.add_argument("--platform", default="ci-platform.yml")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", "-o")
    parser.add_argument("--check", action="store_true", help="Fail if output differs; never modify output")
    parser.add_argument("--platform-schema", action="store_true")
    options = parser.parse_args(argv)
    try:
        if options.command == "schema":
            model = Platform if options.platform_schema else Pipeline
            result = json.dumps(model.model_json_schema(by_alias=True), indent=2) + "\n"
        else:
            pipeline, platform, origins = load(options.config, options.platform)
            sources = source_hashes(options.root, [options.config, options.platform])
            jobs, payload = compile_pipeline(pipeline, platform, sources=sources)
            if options.command == "validate":
                result = f"Valid: {len(pipeline.projects)} projects; {len(payload['nodes']) + 1} jobs. Target GitLab CI Lint is still required.\n"
            elif options.command == "explain":
                result = json.dumps({"generation": "committed top-level GitLab CI", "origins": origins,
                                     "projects": payload["pipeline"]["projects"], "jobs": payload["nodes"]}, indent=2) + "\n"
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
