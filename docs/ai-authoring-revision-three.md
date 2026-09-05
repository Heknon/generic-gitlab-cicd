# AI-assisted authoring — revision three

This is the entry point for agents creating or changing pipelines with generic-ci 0.3.2. It describes implemented interfaces. The older product proposals and scenario audits are design history; do not infer support from those documents.

## Install and invoke the skill

The portable skill is `skills/generic-ci-authoring/`. Copy that entire directory into the agent host's supported skill directory; its references, schemas and examples travel with it. For an agent without skill discovery, explicitly ask it to read that directory's SKILL.md. Do not copy just the entrypoint. Keep the skill and installed CLI from the same reviewed toolkit revision.

Example request:

> Use generic-ci-authoring to configure this repository. Reuse the committed organization source. Inspect the application and package-manager files, add the checks we already run, and configure MR validation and a manual release from main. Validate, explain and render the pipeline. Show the generated jobs and any missing infrastructure settings. Keep changes on the current feature branch.

The configuration-source loader does not automatically install agent skills. Source repositories may distribute skills, but loading a configuration source only consumes its declared templates/defaults. This release has no MCP server: agents invoke the CLI through their shell tool. Do not invent MCP tool names.

## Establish the actual project contract

Read generic-ci.yml and generic-ci.lock.json when present, then the configured delivery file and optional platform overlay. Otherwise inspect delivery.yml and ci-platform.yml. Inspect pyproject.toml/package.json, committed lockfiles, Dockerfiles, chart/values files, existing CI and developer instructions. Use existing commands and paths instead of inventing pytest, a test script or a Dockerfile that does not exist.

Check `generic-ci --help` and `generic-ci source --help`. Inspect installed version with `python -c 'import generic_ci; print(generic_ci.__version__)'` using the CLI's environment. Export the installed schemas when uncertain:

```sh
generic-ci schema -o /tmp/generic-ci-workflows.schema.json
generic-ci schema --platform-schema -o /tmp/generic-ci-platform.schema.json
```

Those schemas describe delivery/platform configuration. Source manifest/project schemas are bundled separately. Schema format version 1 is not toolkit version 0.3.2 or an organization source tag such as v1.4.0.

If the CLI is missing, use the organization's documented internal package index or prepared runtime image. Do not silently download from public registries in an air-gapped workflow. Source templates do not install the CLI or runner images.

## Create or edit

For an existing project, edit its authoring files directly. Do not reinitialize over them. For a new project, use the organization's registered source or explicit repository/ref:

```sh
generic-ci source list
generic-ci init --template node-service
# Without a personal source registration:
generic-ci init --repo ssh://git@gitlab.internal/platform/ci-toolkit.git \
  --ref v1.4.0 --template node-service
```

`init` without a template lists choices when a source is registered. Templates are copied once; developers own their resulting files. Replace starter paths/commands with repository facts. Placeholder Artifactory hosts in this toolkit's defaults need organization configuration before use.

Use only fields in the installed schema. User-defined check names are not toolkit functions. Select checks under the desired workflows. Do not edit generated jobs to introduce a change that will be lost on render; express supported fields in delivery configuration. If the requested behavior is unsupported, explain the missing contract and propose a concrete supported alternative without disguising a custom workaround as native support.

## Validate and deliver

```sh
generic-ci validate
generic-ci explain -o /tmp/generic-ci-explain.json
generic-ci render -o .gitlab-ci.yml
generic-ci render --check -o .gitlab-ci.yml
git diff --check
```

Use `--root /path/to/consumer` when operating elsewhere. Input configuration paths are relative to that root; use an absolute output path if output placement could be ambiguous. For projects without a source descriptor, provide `--config` and `--platform` as needed. Source-backed projects inherit platform settings automatically. `--offline` requires the pinned source objects to be cached.

Review event selection, check/build gates, producer references, release tag conventions, deployment targets and effective image/runner choices in explain output. Locally successful compilation does not run user scripts, check remote image existence, or validate a live GitLab installation. GitLab CI Lint and runtime execution are separate evidence.

Commit authoring files, source descriptor/lock, any referenced project assets and generated CI together when committing is within scope. Report the exact branch, local commands/results and outstanding infrastructure validation. Do not claim deployment success based on rendering. Existing authorization governs commits/pushes; the skill does not require asking again for actions already authorized.

## Troubleshooting

| Symptom | What to inspect or change |
|---|---|
| Source revision not cached | Run source fetch while connected, or import a bundle containing the pinned commit |
| Source lock missing/mismatched | Inspect source changes; source update intentionally establishes a new lock, never hand-edit it |
| Init refuses overwrite | Preserve existing files and edit them; initialize an example in a separate scratch directory if useful |
| Unknown field/check/producer | Read the installed schema and explain output; producer must be enabled in the same event |
| Dependency cycle | Distinguish project change propagation from explicit artifact ordering; remove the actual cycle |
| Missing runtime image | Fix the platform role image or an explicit check override; don't add a public fallback |
| Build true is ambiguous | Select application/container/package explicitly |
| Generated output differs | Regenerate from the reviewed authoring inputs and inspect the diff |
| Candidate rejected in a release | Use an MR/manual test workflow; publication requires normal dependencies |
| Partial Helm baseline missing | First establish a complete deployment with all mapped images |
| Check does not execute after approval | Confirm it is a deployment before.checks reference; an ordinary workflow check ran earlier |
| Native GitLab behavior requested | Check supported execution fields; arbitrary job overrides/rules are not a general escape hatch in this interface |

## Behavioral evaluation tasks

These scenarios are useful for evaluating an agent using the skill. Work in disposable consumers with local source fixtures; no live publication is needed.

1. Existing Python package: keep its test command, select all uv groups, add an MR version check/release button using project.version. Expect no public bootstrap and no new fixed check taxonomy.
2. pnpm SDK/API monorepo: SDK build artifacts feed API contract tests; SDK changes select the API transitively. Expect depends-on plus needs, with the producer enabled in each consuming event; no implicit package replacement.
3. Shared Helm chart: bind API repository/tag to services.api.image paths, use two ordered values files and a pinned remote chart. Expect correct complete/partial prerequisites and no literal image address used as a values path.
4. Source upgrade offline: show that rendering remains pinned after the source branch moves; import the pinned Git bundle into a fresh cache, then explicitly update when requested. Expect starter files preserved.
5. Unsupported request: Windows shell execution, Node candidate container parity or automatic built-image FROM binding. Expect an accurate limitation, not plausible-looking unsupported YAML.

These are evaluation instructions, not evidence that an independent model has passed them. The repository checks schema validity, example compilation and CLI/source mechanics separately.
