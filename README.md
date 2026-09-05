# Generic GitLab CI/CD

**Define your projects and checks. Generate a reviewable GitLab pipeline.**

Generic GitLab CI/CD is a Python toolkit that turns a small delivery configuration into a committed `.gitlab-ci.yml`. It connects your existing test commands, application builds, package publication, container builds, and Helm deployments while keeping infrastructure settings separate from application configuration.

The package is named `generic-gitlab-cicd`; the command is `generic-ci`. GitLab and your runners execute the generated pipeline. The CLI validates and generates configuration locally.

[Quick start](#quick-start) · [Workflow guide](docs/workflows.md) · [CLI reference](docs/cli-reference.md) · [Examples](examples/workflows) · [Documentation](docs/README.md)

## One repository. Tests, packages, and deployment.

Keep your shared Python package and API together. Generic CI tests them, publishes the package on release, and deploys the API to OpenShift.

```yaml
version: 1

projects:
  sdk:
    path: packages/sdk
    python: {}
    checks:
      tests:
        script: [uv run --no-sync pytest]
    package:
      index: internal
    release:
      tag: v{version}
    workflows:
      merge-request:
        checks: [tests]
      release:
        checks: [tests]
        publish: true

  api:
    path: services/api
    depends-on: [sdk]
    python: {}
    checks:
      tests:
        script: [uv run --no-sync pytest]
    container:
      dockerfile: Dockerfile
    release:
      tag: v{version}
      needs: [sdk]
    workflows:
      merge-request:
        checks: [tests]
      release:
        checks: [tests]
        build: [container]

deployments:
  api:
    target: production
    chart:
      path: deploy/chart
    values: [deploy/values.yaml]
    images:
      - from: api.build-image
        set:
          repository: apps.api.image.repository
          tag: apps.api.image.tag
    workflows:
      release:
        when: manual
```

**What you get:**

- **On merge requests:** test affected projects. SDK changes also select the API for testing.
- **On a release tag:** run checks, publish the SDK, and build the API image. The API release depends on SDK publication; its tests and image build can run independently.
- **When you approve deployment:** after the SDK publication and API release complete, deploy the built API image through Helm to OpenShift.

Both projects use a shared version: projects at `1.2.0` release under the protected tag `v1.2.0`. Dependency installation follows each project's declared dependencies and committed lockfile; `depends-on` selects work, not package replacements.

Your organization supplies prepared runtime images, registry settings, and the production target in `ci-platform.yml`. The application's `deploy/chart` and `deploy/values.yaml` describe the deployment. Start with the [shared chart](charts/generic-app) and [values example](examples/helm-values.yaml); configure the named `internal` publishing index and CI credentials through your platform.

## Quick start

Use Python **3.11 or newer**. From a checkout of this toolkit, install the CLI into an isolated environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Then, from your application repository:

```sh
generic-ci setup
```

Setup guides you through organization templates or standalone configuration, previews files before writing, and includes local editor schemas. Start with its single-app configuration and add projects as needed, or choose an organization template for your monorepo. See the [setup guide](docs/setup.md) for prepared images, unattended options, and editor integration.

After editing your configuration:

```sh
generic-ci doctor
generic-ci explain
generic-ci render -o .gitlab-ci.yml
```

Tests-only setup needs a prepared runtime and runner tag; builder and registry settings are added when you choose container delivery.

Review and commit `delivery.yml`, your platform configuration, and the generated `.gitlab-ci.yml` together. Validate with `generic-ci lint --gitlab-url https://gitlab.internal --project group/app` using a token in `GENERIC_CI_GITLAB_TOKEN`, then push your branch.

**Prefer autocomplete?** Setup exports the schemas automatically. You can also refresh them from the installed CLI:

```sh
generic-ci schema -o .generic-ci/delivery.schema.json
generic-ci schema --platform-schema -o .generic-ci/platform.schema.json
```

Associate them with `delivery.yml` and `ci-platform.yml` in your editor. The [editor guide](docs/setup.md#editor-completion-and-validation) includes PyCharm instructions.

Inspect one change before pushing:

```sh
generic-ci explain --event merge-request --changed services/api/main.py
```

Chart and values changes automatically select their deployments. GitLab omits unaffected jobs while retaining required producers. See [upgrading from 0.3](docs/migration-0.4.md) for the 0.4 release/runtime changes.

## Add the delivery features you need

| Goal | Configuration / next step |
| --- | --- |
| Test Node, pnpm, or Bun projects | Declare `node.package-manager`, commit the corresponding lockfile, and use your existing commands; see the [workflow guide](docs/workflows.md) |
| Build application artifacts | Set `build.script` and `build.outputs`; select `build: [application]` in the workflow |
| Retest downstream projects | Add project `depends-on: [sdk]`; this propagates change selection |
| Transfer generated files to another job | Add check/build `needs: [sdk.build]`; the producer must be enabled in the same event |
| Build and push containers | Configure `container` and select `build: [container]`; the default builder is Buildah |
| Publish Python or npm-compatible packages | Configure `package` and workflow `publish`; see [publication behavior](docs/workflows.md#package-publication--revision-four) |
| Coordinate releases | Configure project versions, tag conventions, and optional `release.needs`; see the [release workflow](docs/workflows.md#version-bumps-and-release-button) |
| Deploy to OpenShift | Configure a Helm deployment, target, values, and image bindings; start with the [deployment example](examples/workflows/delivery.yml) and [chart values](examples/helm-values.yaml) |

`depends-on` and `needs` have different jobs: the first selects affected projects; the second transfers explicit same-event outputs. Neither implicitly installs an unreleased package. Candidate dependency testing has a separate contract described in the [workflow guide](docs/workflows.md).

## Share organization defaults

An organization source can provide platform defaults and starter templates. Initialize from a reviewed source revision:

```sh
generic-ci init --repo ssh://git@gitlab.example.internal/platform/ci-config.git \
  --ref v1.0.0 --template python-service
```

The repository, ref, and template name above are examples; the source must contain the toolkit's source manifest. Initialization copies starter files and records an exact source commit. Commit the resulting descriptor and lockfile. Later source updates change inherited defaults while preserving consumer-owned template files.

For disconnected authoring, cache the locked source or import a Git bundle, then render with `--offline`. This flag controls configuration-source acquisition; it does not enforce network isolation inside application jobs. See [configuration sources](docs/configuration-sources.md) for source layout, registration, updates, and offline setup.

## OpenShift deployment

The bundled [generic-app chart](charts/generic-app) supports multiple applications, Services, OpenShift Routes with TLS, probes, resources, ports, volumes/mounts, and pod labels/annotations. Images use **repository + tag**; Buildah also records the pushed digest as evidence. Security contexts are configurable, with cluster defaults applying when unset.

Helm deployments support complete updates and partial updates that preserve unchanged service images. Partial updates require a compatible existing baseline; chart or configuration changes require a complete deployment. MR previews require a nonproduction target. Production requires a protected ref, and candidate dependency runs cannot update persistent deployments.

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

The local pipeline fixtures use pinned `gitlab-ci-local`. Real GitLab/Runner E2E runs are deliberate, through workflow dispatch or an opt-in PR label. Use focused tests while iterating; see [testing commands and evidence](docs/testing.md) for prerequisites, integration tests, release checks, and scenario selection.

Version 0.3.2 passed [fast CI](https://github.com/Heknon/generic-gitlab-cicd/actions/runs/33975049919) and [six real GitLab E2E scenarios](https://github.com/Heknon/generic-gitlab-cicd/actions/runs/33975818144). That evidence covers the tested pipeline fixtures, not deployment to your registry or OpenShift cluster. Qualify Buildah execution, registry access, Route admission, and rollout/rollback on your infrastructure before production use. Workflow runtime execution currently targets Linux; Windows workflow execution is not supported.

## Documentation

- [CLI commands, file ownership, and path rules](docs/cli-reference.md)
- [Checks, builds, releases, and deployment workflows](docs/workflows.md)
- [Organization configuration sources](docs/configuration-sources.md)
- [AI-assisted authoring and portable skill](docs/ai-authoring.md)
- [Publishing the toolkit to PyPI](docs/pypi-release.md)
- [Full documentation map](docs/README.md)
