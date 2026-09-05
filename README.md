# Generic GitLab CI/CD

**Define your projects and checks. Generate a reviewable GitLab pipeline.**

Generic GitLab CI/CD is a Python toolkit that turns a small delivery configuration into a committed `.gitlab-ci.yml`. It connects your existing test commands, application builds, package publication, container builds, and Helm deployments while keeping infrastructure settings separate from application configuration.

The package is named `generic-gitlab-cicd`; the command is `generic-ci`. GitLab and your runners execute the generated pipeline. The CLI validates and generates configuration locally.

[Quick start](#quick-start) · [Workflow guide](docs/workflows-revision-one.md) · [CLI reference](docs/cli-reference.md) · [Examples](examples/workflows) · [Documentation](docs/README.md)

## Why use it?

Use this toolkit when several services or repositories share delivery conventions, but each team needs to choose its own checks and release behavior.

- **Keep application intent readable.** Name your checks and select them for pushes, merge requests, releases, manual pipelines, or schedules. The toolkit does not insert an assumed test suite.
- **Reuse platform configuration.** Share runner tags, prepared images, registries, and deployment targets through organization defaults and Git-backed templates.
- **Connect monorepo work explicitly.** Declare which projects are affected by upstream changes and which jobs need upstream artifacts. Artifact receipts verify the producing commit, pipeline, configuration, and file checksums.
- **Support internal infrastructure.** Use prepared runtime images, internal package services, and cached configuration sources. Consumer jobs have no automatic public toolkit bootstrap.
- **Review generated changes before execution.** Commit the generated YAML alongside its inputs; validate configuration and detect generation drift locally or in CI.

For a repository with a few standalone jobs, handwritten GitLab CI may be sufficient. This toolkit is most useful when repeated release, dependency, and deployment rules are becoming difficult to maintain consistently.

## How it works

| File | What belongs here |
| --- | --- |
| `delivery.yml` | Projects, commands, workflow selection, builds, and deployments |
| `ci-platform.yml` | Runtime images, runner tags, registry locations, and deployment targets |
| `.gitlab-ci.yml` | Generated pipeline; regenerate after editing the inputs |
| `generic-ci.yml` + `generic-ci.lock.json` | Optional organization source configuration and its pinned commit |

Run `generic-ci validate`, inspect `generic-ci explain`, then run `generic-ci render`. Commit the inputs and generated pipeline together. GitLab executes a planner and the selected runtime jobs using your prepared images.

## Quick start

Install the CLI as shown below, then run **`generic-ci setup`** from your application repository. It walks you through organization templates or standalone configuration, validates the result, and previews files before writing. It never overwrites existing files or installs a pre-commit hook.

```sh
generic-ci setup
```

Organization mode asks for a Git configuration repository (GitHub or GitLab), revision, template, and application settings. Standalone mode detects the ecosystem, asks for your existing test command and runtime infrastructure, and optionally creates a manual OpenShift MR preview with `deploy/values.yaml`. The chart must already be published and compatible with generic-app 2.x; setup does not provision infrastructure.

Both paths write the generated pipeline, setup notes, and local editor schemas. See [setup and editor integration](docs/setup-revision-one.md) for unattended flags, dry runs, schema mappings, and limitations.

The following manual walkthrough explains the files setup produces and remains useful when editing an existing configuration.

This example adds push and merge-request tests to an **existing Python project**. It assumes the repository already has a `pyproject.toml`, a committed `uv.lock`, and pytest declared in a dependency group. Replace the test command if your project uses something else.

### 1. Install the CLI

Use Python **3.11 or newer**. From a checkout of this repository, install into an isolated environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
generic-ci --help
```

For repeatable organization setup, distribute a versioned wheel through your approved internal package index or wheelhouse. Keep the authoring CLI and the toolkit installed in runner images on the **same version**. This source revision is **0.3.3**; source availability does not imply that version has been published to PyPI.

The install command uses your configured package sources. In an air-gapped environment, prepare the wheel and all dependencies internally first.

### 2. Provide the runtime configuration

In your **application repository**, create `ci-platform.yml`:

```yaml
version: 1
defaults:
  tags: [internal-linux]
images:
  python: registry.example.internal/ci/python-toolkit:0.3.3
container-builder:
  engine: buildah
  image: registry.example.internal/ci/buildah-toolkit:0.3.3
registries:
  containers: registry.example.internal/apps
  previews: registry.example.internal/previews
allowed-hosts:
  - registry.example.internal
  - gitlab.example.internal
variables:
  UV_PYTHON_DOWNLOADS: never
```

**Replace the example addresses and tags with your platform's values.** These images are placeholders, not publicly available toolkit images. The Python image needs Python, Git, uv, the matching toolkit, and your internal package/CA configuration. The builder image needs Buildah and the matching Python toolkit runtime. Builder and registry settings are required by the platform schema even though this test-only example does not build or push an image.

If your organization already provides a platform file or configuration source, use it. Platform maintainers can start with the [image-factory setup guide](starters/image-factory/CI-SETUP.md). Installing the CLI on your laptop does not prepare runner images.

### 3. Define the checks

Create `delivery.yml` in the application repository:

```yaml
version: 1
projects:
  app:
    path: .
    python:
      groups: all
    checks:
      unit:
        script:
          - uv run --no-sync pytest
    workflows:
      push:
        checks: [unit]
      merge-request:
        checks: [unit]
```

`path` is relative to the repository root. Commands run in that project directory. Python dependency preparation uses the committed lockfile; `--no-sync` prevents the test command from resynchronizing the environment. With `groups: all`, dependency groups must be mutually compatible.

This configuration selects `unit` for push and merge-request workflows. Push pipelines are suppressed when an open MR takes their place. It does not publish a package, build an image, or deploy an application.

### 4. Validate, inspect, and generate

Run these commands from the application repository using the installed CLI:

```sh
generic-ci validate
generic-ci explain -o ci-explain.json
generic-ci render -o .gitlab-ci.yml
generic-ci render --check -o .gitlab-ci.yml
```

Review `ci-explain.json` and the generated jobs, then commit `delivery.yml`, `ci-platform.yml`, and `.gitlab-ci.yml`. The explanation file is optional diagnostic output.

Validate the generated YAML with **your GitLab CI Lint**, push the branch, and inspect the first pipeline. Local validation checks configuration and the job graph; it does not run pytest or check that a registry image exists. Your runner must match the configured tags and be able to pull the runtime image and reach the configured internal services.

After changing a command, platform setting, or source lock, render again. Use `generic-ci render --check -o .gitlab-ci.yml` in CI to detect stale generated YAML.

## Add the delivery features you need

| Goal | Configuration / next step |
| --- | --- |
| Test Node, pnpm, or Bun projects | Declare `node.package-manager`, commit the corresponding lockfile, and use your existing commands; see the [workflow guide](docs/workflows-revision-one.md) |
| Build application artifacts | Set `build.script` and `build.outputs`; select `build: [application]` in the workflow |
| Retest downstream projects | Add project `depends-on: [sdk]`; this propagates change selection |
| Transfer generated files to another job | Add check/build `needs: [sdk.build]`; the producer must be enabled in the same event |
| Build and push containers | Configure `container` and select `build: [container]`; the default builder is Buildah |
| Publish Python or npm-compatible packages | Configure `package` and workflow `publish`; see [publication behavior](docs/workflows-revision-one.md#package-publication--revision-four) |
| Coordinate releases | Configure project versions, tag conventions, and optional `release.needs`; see the [release workflow](docs/workflows-revision-one.md#version-bumps-and-release-button) |
| Deploy to OpenShift | Configure a Helm deployment, target, values, and image bindings; start with the [deployment example](examples/workflows/delivery.yml) and [chart values](examples/helm-values.yaml) |

`depends-on` and `needs` have different jobs: the first selects affected projects; the second transfers explicit same-event outputs. Neither implicitly installs an unreleased package. Candidate dependency testing has a separate contract described in the [workflow guide](docs/workflows-revision-one.md).

## Share organization defaults

An organization source can provide platform defaults and starter templates. Initialize from a reviewed source revision:

```sh
generic-ci init --repo ssh://git@gitlab.example.internal/platform/ci-config.git \
  --ref v1.0.0 --template python-service
```

The repository, ref, and template name above are examples; the source must contain the toolkit's source manifest. Initialization copies starter files and records an exact source commit. Commit the resulting descriptor and lockfile. Later source updates change inherited defaults while preserving consumer-owned template files.

For disconnected authoring, cache the locked source or import a Git bundle, then render with `--offline`. This flag controls configuration-source acquisition; it does not enforce network isolation inside application jobs. See [configuration sources](docs/configuration-sources-revision-two.md) for source layout, registration, updates, and offline setup.

## OpenShift deployment

The bundled [generic-app chart](charts/generic-app) supports multiple applications, Services, OpenShift Routes with TLS, probes, resources, ports, volumes/mounts, and pod labels/annotations. Images use **repository + tag**; Buildah also records the pushed digest as evidence. Security contexts are configurable, with cluster defaults applying when unset.

Helm deployments support complete updates and partial updates that preserve unchanged service images. Partial updates require a compatible existing baseline; chart or configuration changes require a complete deployment. Production requires a protected ref, and candidate dependency runs cannot update persistent deployments.

You supply registry credentials, CA trust, a kubeconfig, namespace resources, and application values. The toolkit does not provision the cluster, issue certificates, create PVCs automatically, or make multi-project publication transactional. Keep release image tags immutable: the shared chart uses `IfNotPresent`.

## Existing component users

The repository also contains [low-level GitLab CI/CD components](templates) and the older compiler, available through `--format legacy`. They are separate interfaces from the workflow configuration above. New users should start with the CLI walkthrough.

Mirror components into your GitLab instance and pin includes to an immutable revision. Consult the [component air-gap guide](docs/airgap.md) for setup. Older BuildKit/digest-only Helm adapters target chart 1.x; the current chart uses OpenShift Routes and repository/tag values. Do not mix those contracts without migrating the configuration.

## Development and validation

From a toolkit checkout with Python, Git, Node/npm, rsync, and Helm available:

```sh
python -m pip install . build twine uv setuptools
npm ci --ignore-scripts --no-audit --no-fund
python scripts/sync_embedded.py --check
python scripts/sync_authoring_skill.py --check
python -m unittest discover -s tests -v
npm run test:ci-local
```

The local pipeline fixtures use pinned `gitlab-ci-local`. Real GitLab/Runner E2E runs are deliberate, through workflow dispatch or an opt-in PR label. Use focused tests while iterating; see [testing commands and evidence](docs/testing-revision-one.md) for prerequisites, integration tests, release checks, and scenario selection.

Version 0.3.2 passed [fast CI](https://github.com/Heknon/generic-gitlab-cicd/actions/runs/33975049919) and [six real GitLab E2E scenarios](https://github.com/Heknon/generic-gitlab-cicd/actions/runs/33975818144). That evidence covers the tested pipeline fixtures, not deployment to your registry or OpenShift cluster. Qualify Buildah execution, registry access, Route admission, and rollout/rollback on your infrastructure before production use. Workflow runtime execution currently targets Linux; Windows workflow execution is not supported.

## Documentation

- [CLI commands, file ownership, and path rules](docs/cli-reference.md)
- [Checks, builds, releases, and deployment workflows](docs/workflows-revision-one.md)
- [Organization configuration sources](docs/configuration-sources-revision-two.md)
- [AI-assisted authoring and portable skill](docs/ai-authoring-revision-three.md)
- [Publishing the toolkit to PyPI](docs/pypi-release.md)
- [Full documentation map](docs/README.md)
