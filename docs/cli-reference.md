# CLI reference

The installed CLI and generated JSON schemas are authoritative for accepted fields. Current default format is workflows; legacy authoring requires `--format legacy`.

| Command | Purpose and effects |
|---|---|
| `generic-ci setup` | Guided organization/standalone setup, schemas, validation and rendering; previews writes and refuses overwrites |
| `generic-ci setup --yes --dry-run ...` | Validate supplied answers without prompts or project writes |
| `generic-ci init --template NAME` | Copy a starter from the default source and write the project descriptor/lock; never overwrite files |
| `generic-ci init --source NAME` | List templates from a named registration |
| `generic-ci init --repo URL --ref REF --template NAME` | Initialize directly from Git without a global registration |
| `generic-ci init --ecosystem python` | Built-in starter when no source registration is present; npm/pnpm/bun also accepted |
| `generic-ci schema` | Export workflow JSON Schema; `--platform-schema` exports platform schema |
| `generic-ci validate` | Load/merge/schema-check and compile the job graph; no user scripts execute |
| `generic-ci explain` | Readable commands, job dependencies, manual gates and runtime images; `--json` includes effective configuration and origins |
| `generic-ci explain --event merge-request --changed services/api/main.py` | Simulate affected selection without executing commands |
| `generic-ci doctor` | Check local project inputs and report external infrastructure requirements |
| `generic-ci lint --gitlab-url URL --project ID` | Compile and validate with the target GitLab CI Lint API; never create a pipeline |
| `generic-ci upgrade` | Preview schemas, source and runtime binding updates; `--apply` writes after successful staging |
| `generic-ci render -o .gitlab-ci.yml` | Generate the top-level pipeline and input fingerprints |
| `generic-ci render --check -o .gitlab-ci.yml` | Compare generation with the existing file without writing; nonzero on drift |
| `generic-ci source add NAME --repo URL --ref REF --default` | Fetch/validate and register a source locally, recording the initial pin |
| `generic-ci source list` | Print registered sources and the default |
| `generic-ci source fetch` | Cache the current project's locked commit without changing its lock |
| `generic-ci source fetch --from PATH --offline` | Import locked objects from a Git checkout/bundle |
| `generic-ci source update --ref REF --check` | Show proposed source defaults changes; validate; nonzero if lock would change |
| `generic-ci source update --ref REF` | Validate new defaults, update source descriptor/lock, preserve template files |

Authoring commands accept `--root`, `--config`, `--platform`, `--output`/`-o`, and `--offline`. Output paths are relative to the shell working directory, not automatically to --root. `--offline` governs source acquisition; it does not simulate offline execution of generated jobs. Without generic-ci.yml, the standalone config/platform files are used. `--platform` on a source-backed project supplies an overlay. Source fetch/update accept their own --root flag; use subcommand help instead of assuming every authoring flag applies to source commands.

The CLI does not trigger or poll pipelines, publish packages, deploy Helm releases, install AI skills, or launch an MCP server. Generated runtime jobs perform build/publication/deployment actions later under GitLab.

## File ownership

| File | Owner and update behavior |
|---|---|
| delivery.yml | Developer-owned project/check/workflow/deployment declarations |
| generic-ci.yml | Project-owned source repository/ref and input paths |
| generic-ci.lock.json | CLI-generated exact source commit/defaults; commit it, update via source update |
| ci-platform.yml | Optional project platform overlay, or standalone platform configuration |
| .gitlab-ci.yml | Generated output; edit authoring inputs and render again |
| generic-ci-source.yml | Organization source manifest; points to templates and defaults |
| defaults/platform.yml | Organization infrastructure defaults; no credentials |
| Source template files | Maintained centrally until init copies them; subsequently consumer-owned |

## Public workflow references

References connect same-event producers to consumers:

- `project.check-name`: artifacts explicitly listed in that check's outputs.
- `project.build`: application build outputs.
- `project.build-image`: Buildah repository/tag/digest metadata.
- `project.build-package`: built distribution files and metadata.
- `project.publish`: package publication, only when selected.
- `project.release`: release completion, without a GitLab API call unless `release.gitlab: true`.
- `release.needs: [project]`: coordinated release completion, requiring a common tag convention. Completion waits for configured package publication; deployment waits for completion.

Within a project, needs may omit the project prefix. Deployment check references include project and check name. Generated job IDs can be inspected in explain/render but are not a stable promise that arbitrary GitLab overrides compose with the authoring schema.

## Path and execution contracts

| Setting | Relative to / meaning |
|---|---|
| project.path | Consumer repository root |
| check.script and build.script working directory | Project directory |
| check.outputs and build.outputs | Project directory; verified files are handed to downstream jobs at repository-relative paths |
| container.dockerfile and container.context | Project directory |
| node.workspace | Repository root |
| release.version.file | Project directory |
| deployment.chart.path and deployment.values | Repository root; values files applied in listed order |
| check.artifacts paths | Repository root; native GitLab reports are not rewritten |
| container.secrets values | Names of file-type CI variables, not secrets themselves |

Checks can customize image, tags, timeout, variables, services, parallel matrix and artifacts. Maps merge and lists replace. Selected matrix instances all gate dependent builds. Shared outputs from a check matrix are rejected. Runtime role images require compatible 0.4.x toolkit code, Python, Git and Bash; a bare arbitrary Python/Node image is insufficient.

## Recommended pre-commit synchronization

Add rendering to pre-commit so edits to delivery configuration, source locks or platform overrides regenerate the committed pipeline. With the pre-commit framework, add this local hook to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: generic-ci-render
        name: Regenerate GitLab CI
        entry: generic-ci render -o .gitlab-ci.yml --offline
        language: system
        pass_filenames: false
        always_run: true
```

Install the hook with `pre-commit install`. Both pre-commit and the matching generic-ci CLI must already be installed in the developer environment. Cache the project's pinned source with `generic-ci source fetch` (or import its Git bundle) before committing; --offline prevents a commit from unexpectedly fetching source configuration. Standalone configurations without a source descriptor do not require this cache.

The hook runs at the repository root. If your input/output paths differ, adjust the command accordingly. When rendering modifies .gitlab-ci.yml, pre-commit stops the commit: review the generated diff, stage the updated file and commit again. Do not automatically stage generated changes from the hook.

Keep a separate CI check because local hooks can be skipped:

```sh
generic-ci render --check -o .gitlab-ci.yml --offline
```

Run it in a job with the matching CLI and pinned source cache available. This checks synchronization only; it does not replace GitLab CI Lint or executing the pipeline. Teams that prefer a hook that never modifies files can use this --check command as the hook entry instead and render explicitly when it fails.

## Runtime validation changes in 0.3.2

Selected checks cannot have an empty matrix or collide with generated job names such as version/build-image/publish. Complete release deployments require a shared tag convention; use partial updates for independently tagged images with an existing baseline. Entries in one check/build script execute in one shell, preserving exports and working-directory changes. Deployment ancestry checks fetch missing shallow history from origin before deciding whether an update is stale.

## Setup and editor schemas

See [setup — revision one](setup.md). `generic-ci setup --help` lists setup-specific flags. Setup writes `.generic-ci/delivery.schema.json` and `.generic-ci/platform.schema.json` from the installed CLI and adds YAML schema comments. Refresh them after upgrading with `generic-ci schema -o .generic-ci/delivery.schema.json` and `generic-ci schema --platform-schema -o .generic-ci/platform.schema.json`. Map these to authoring YAML, not the generated GitLab YAML or Helm values.

## Diagnose, simulate and upgrade

`doctor` returns nonzero for missing local inputs: project directories, manifests, lockfiles, version fields, Dockerfiles, local charts/values and declared publishing destinations. It never executes application commands. `--runtime-role python` (or control/node/bun/helm/builder/all) additionally checks local tool availability, useful inside a prepared role image. Remote image contents, credentials, registry/cluster permissions and rollout are reported as unverified.

`explain --event release --tag v1.2.0` previews a release graph. `--changed` accepts repository-relative paths; an empty list means no files changed. Omit it for a complete event. Simulation assumes branch, protected-ref and credential requirements are met; it does not contact GitLab. `--json` provides machine-readable selection reasons and effective source origins.

For GitLab CI Lint, use `GENERIC_CI_GITLAB_TOKEN` or `GITLAB_TOKEN` in the environment. The HTTPS GitLab host must be in platform `allowed-hosts`. `--ref main --simulate` requests GitLab's pipeline-creation simulation; no jobs are run. `--offline` cannot be used with lint. See the [GitLab CI Lint API](https://docs.gitlab.com/api/lint/).

Upgrade from an installed newer CLI:

```sh
generic-ci upgrade --check
generic-ci upgrade --runtime-image python=registry.internal/ci/python:0.4.0
# After reviewing the proposed diff:
generic-ci upgrade --runtime-image python=registry.internal/ci/python:0.4.0 --apply
```

Repeat `--runtime-image ROLE=IMAGE` for control/python/node/bun/helm/builder. New hosts must first be reviewed and added to the platform allowlist. `--source-ref REF` stages a source update together with schema/pipeline changes; it requires connected source acquisition. Without it, the existing source pin is preserved and `--offline` is supported. Source templates/application files stay consumer-owned. Upgrade validates the staged graph before writing and rolls back its file writes on a write error. It does not install Python packages or build/pull images. Output defaults to `--root/.gitlab-ci.yml`; an explicit `-o` is shell-relative and must remain inside the project.

Use `generic-ci --version` and each subcommand's `--help` for the installed interface. See [0.4 migration](migration-0.4.md) before upgrading from 0.3.
