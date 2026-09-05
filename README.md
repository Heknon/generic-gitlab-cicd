> Feature-branch workflow interface (0.3.0): see [implemented revision-one guide](docs/workflows-revision-one.md), [configuration examples](examples/workflows), and [JSON Schema](schemas/workflows.schema.json). This is a review build with documented integration gates. Older prototype CLI commands now require `--format legacy`; existing GitLab components remain available.

# Generic GitLab CI components — revision one

A self-contained component repository for GitLab, including an offline-capable runtime-image factory and a generic Kubernetes Helm chart. The recommended Python entry point is `examples/uv-airgap.yml`.

This is an implementation to configure and validate on your installation. It has not been run against your GitLab, Artifactory or Kubernetes cluster. Infrastructure addresses, credentials, approved base images and application deployment settings must be supplied by your organization.

## Components

| Component | Purpose |
| --- | --- |
| `workflow` | Stages, cancellation of superseded work, suppression of duplicate push/MR pipelines |
| `task` | Language-neutral lint, test, build or verification commands; reports, caches and artifacts |
| `uv-test` | Locked uv validation, optional Git dependency replacement and commit provenance |
| `version-check` | PEP 440 version increase against the current target branch; tag/version matching |
| `python-package` | Build wheel/sdist once, check metadata, retain distribution artifacts |
| `python-publish` | Publish those artifacts to PyPI, TestPyPI, GitLab or Artifactory |
| `container-preview-build` | Same-project MR builds restricted to a dedicated preview repository |
| `container-build` | Rootless BuildKit build, digest metadata, optional protected-ref registry push |
| `deploy` / `preview` | Provider-neutral command adapters with deployment locking and preview cleanup |
| `helm-deploy` / `helm-preview` | Helm rollout with failure rollback and isolated review environments |
| `release` | Create a GitLab release for an existing protected tag |

Each YAML file documents its own typed inputs. Include components multiple times with unique job names. Secrets remain GitLab CI variables; they are never component inputs.

## Bootstrap

1. Import this directory into a GitLab project such as `platform/ci-components` on your own GitLab instance. Component includes cannot directly cross to an unrelated GitLab instance; mirror this repository internally.
2. Configure the variables in `docs/airgap.md`, and supply approved internal Python, BuildKit and Helm runtime images. The repository's own pipeline uses those images. Do not switch all consumers until the runtime images are available.
3. Adapt non-secret files under `images/python/config/`, add approved CA certificates, and review `images/python/requirements.lock`. Run `images.gitlab-ci.yml` to build the Python runtime from your internal package index and push it to Artifactory. Initial BuildKit and Python base images must already be mirrored and trusted.
4. Run this repository's validation pipeline. Publish/tag an immutable component version, e.g. `1.0.0`. Optionally mark the project as a CI/CD Catalog resource and create a GitLab release. Consumers may pin a full commit SHA before the first release.
5. Copy the relevant example as the consuming project's `.gitlab-ci.yml`. Replace `platform/ci-components`, the release ref and deployment placeholders.
6. Validate the expanded pipeline with your GitLab CI Lint, then run MR, default-branch and protected-tag scenarios before enabling production publishing.

Use a supported GitLab with CI/CD components and array inputs (GitLab 17+ syntax baseline). The optional native release job also requires a GitLab-compatible glab runtime; verify server/CLI compatibility. Runtime jobs target Linux container runners, not Windows shell runners.

## Minimal uv project

```yaml
variables:
  CI_DEPENDENCY_REPO: ""
  CI_DEPENDENCY_REF: ""
  CI_DEPENDENCY_PACKAGE: ""
  CI_DEPENDENCY_OVERRIDES: '[]'

include:
  - component: $CI_SERVER_FQDN/platform/ci-components/workflow@1.0.0
  - component: $CI_SERVER_FQDN/platform/ci-components/uv-test@1.0.0
    inputs:
      name: api-test
      repo: $CI_DEPENDENCY_REPO
      ref: $CI_DEPENDENCY_REF
      package: $CI_DEPENDENCY_PACKAGE
      overrides-json: $CI_DEPENDENCY_OVERRIDES
```

Set `PYTHON_CI_IMAGE` to your internal runtime image. Commit `uv.lock`. Put pytest and application test dependencies in the project's dependency groups. `uv-test` syncs all groups; define mutually compatible groups. Tests run with `uv run --no-sync` so uv does not undo a candidate installation.

## Cross-repository dependencies

For an unreleased dependency, start a pipeline with:

```text
CI_DEPENDENCY_REPO=https://gitlab.internal/team/shared-sdk.git
CI_DEPENDENCY_REF=feature/new-api
CI_DEPENDENCY_PACKAGE=shared-sdk
```

For several dependencies or a package in a repository subdirectory:

```json
[
  {"repo":"https://gitlab.internal/team/sdk.git", "ref":"feature/new-api", "package":"shared-sdk"},
  {"repo":"https://gitlab.internal/team/core.git", "ref":"0123456789012345678901234567890123456789", "package":"core-utils", "subdirectory":"packages/utils"}
]
```

Set that array as `CI_DEPENDENCY_OVERRIDES`. Both mechanisms can be combined if package names do not overlap.

The helper resolves each branch/tag to a commit, applies temporary `tool.uv.override-dependencies`, removes conflicting uv source mappings for the named distribution, re-resolves, and verifies installed `direct_url.json` commit metadata. Original pyproject and lock bytes are restored even if sync fails. A successful override run saves requested refs and actual commits as a provenance artifact. A branch can move between pipelines; use the recorded SHA for reproducible reruns.

The normal path uses `uv sync --locked --all-groups`. The override path deliberately re-resolves and can change transitive dependencies; it tests candidate compatibility rather than certifying the production lock. Overrides must name an installed distribution; a typo or irrelevant package fails verification.

Configure read-only Git authentication in the job's `setup` or runner credential helper. Use credential-free repository URLs. For CI_JOB_TOKEN access, allowlist the consumer project in each dependency project. Restrict the credential helper to the intended host. Do not put credentials in URLs, artifacts, images, or tracked config.

Run a uv workspace from its root; its virtual environment must be `.venv` there. For independent monorepo projects, include one `uv-test` per project and use unique report/provenance paths. Custom UV_PROJECT_ENVIRONMENT layouts require adapting the helper's virtual-environment path.

## Coordinated releases

Candidate testing solves the validation deadlock. It does not make multiple registry publications transactional.

1. Validate the application and dependency candidates together using immutable overrides.
2. Publish the dependency's real version to the internal package repository.
3. Update/validate the application's production lock against that registry, with overrides cleared.
4. Build once, publish/deploy the application, then create its GitLab release.

A dependency package can usually be published before the application is deployed; publishing the wheel does not deploy a running service. For stronger coordination, use an internal candidate repository and promote approved packages before deployment. Two independent GitLab projects still need an orchestration policy; this framework does not promise atomic cross-project rollback. Multi-project trigger/approval coordination is an extension point, not implemented here.

The standard publish and production deploy jobs reject nonempty `CI_DEPENDENCY_*` override variables. Do not hard-code candidate inputs in a production pipeline. Preview artifacts may intentionally contain candidate dependencies; never promote those to production without a clean release validation.

## Versioning, tags and releases

`version-check` reads static `[project].version`. In branch/MR pipelines it fetches the current target branch and requires a strict PEP 440 increase. On tags it requires the tag suffix to match the package version. The default prefix is `v`; use `sdk-v` for independent package tags. Dynamic SCM versions require a custom command via `task`.

Use change rules to restrict bump checks to meaningful package changes if documentation-only MRs should not bump a version. The default checks every selected non-default branch pipeline. `allow-new-package` explicitly permits a manifest absent from the target branch.

Tags are an input to the release pipeline. Create and protect them through your established process; CI does not silently create or push tags. Package publishing and production deployment are blocking manual jobs on protected tags by default. The GitLab release runs last, using `CHANGELOG.md` or a configured notes file. Existing GitLab release retries require deliberate handling; no overwrite or package `--skip-existing` behavior hides duplicate releases.

A tag check validates manifest metadata, not the version assigned by every possible custom build backend. The build defaults use static PEP 621 metadata. Custom build commands should add wheel metadata/version assertions if they derive or rewrite versions.

## Monorepos and cost

`examples/monorepo.yml` demonstrates independent service rules, shared dependency paths and service-specific tag names. Include every shared source/config/lock path that can affect a service; transitive dependency graph discovery is not automatic. `CI_FULL_PIPELINE=true` and schedules run full verification. Tags rebuild all package units in that example, so a release never consumes artifacts from a different pipeline.

The defaults preserve stage barriers: lint → test → build → verify → publish → deploy → release. Jobs use `dependencies` only to select artifacts. No default `needs: []` bypasses validation. All enabled tests must pass before any build proceeds. Stage barriers are conservative across unrelated services; customize explicit DAG dependencies only after keeping release gates intact.

Tests fail normally; only runner/system failures retry once. Deploy/publish operations never retry automatically. Superseded interruptible work can be cancelled. Use `task` cache controls for package download caches, with lockfile-derived keys; keep protected/unprotected cache separation enabled. uv's download cache is safe to rebuild and is not proof of dependency correctness.

## Helm deployment

Use `charts/generic-app`, `examples/helm-values.yaml`, and `docs/kubernetes.md`. Helm manages the application release; Terraform is appropriate for cluster, DNS, network and registry infrastructure outside this repository's scope.

Deploy digest-pinned images from the exact build artifacts. `helm-deploy.image-map-json` maps application names to image repositories and BuildKit metadata files; the helper extracts digests into an overlay passed to Helm. One Helm release may deploy several services, or use separate releases for independently deployable services.

## Validation

```sh
python -m pip install -r requirements-dev.txt
python scripts/sync_embedded.py --check
python -m unittest discover -s tests -v
python tests/integration_uv.py
helm lint charts/generic-app -f examples/helm-values.yaml --strict
helm template smoke charts/generic-app -f examples/helm-values.yaml
```

The integration test uses local Git repositories and URL rewriting; it does not contact a real dependency host or publish anything. The lightweight component expander tests inputs, names and artifact references. It is not GitLab's server-side compiler. Do not interpret local tests as proof that a specific runner, RBAC policy, Artifactory endpoint or cluster deployment works.

## Official references

- GitLab components: https://docs.gitlab.com/ci/components/
- GitLab input types: https://docs.gitlab.com/ci/inputs/
- GitLab environment teardown: https://docs.gitlab.com/ci/environments/
- Rootless BuildKit: https://docs.gitlab.com/ci/docker/using_buildkit/
- GitLab releases: https://docs.gitlab.com/user/project/releases/release_cicd_examples/
- uv overrides: https://docs.astral.sh/uv/concepts/resolution/
- uv configuration: https://docs.astral.sh/uv/reference/settings/
- PyPI trusted publishing: https://docs.pypi.org/trusted-publishers/using-a-publisher/
- Helm upgrade: https://helm.sh/docs/helm/helm_upgrade/
