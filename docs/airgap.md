# Self-managed GitLab and air-gapped Artifactory

Air-gapped here means no public network access. Jobs may reach approved internal GitLab, Artifactory, Kubernetes and DNS services. UV_OFFLINE is a different mode: it disables even internal downloads and only works with a fully populated cache. Do not enable it merely because the network is air-gapped.

## Configuration contract

Set applicable values as group/project variables. Addresses below are examples, not working infrastructure.

| Variable / input | Purpose and placement |
| --- | --- |
| `PYTHON_CI_IMAGE` | Internal digest-pinned Python/Git/uv/twine/build/packaging/tomlkit/ruff/PyYAML runtime |
| `PYTHON_BASE_IMAGE` | Internal Python 3.12+ base with pip, Git, CA tools and initial internal trust; image-factory bootstrap |
| `BUILDKIT_CI_IMAGE` | Internal rootless BuildKit image, including registry CA trust |
| `HELM_CI_IMAGE` | Internal Helm 3 or 4 plus Python 3 and PyYAML; add kubectl/auth plugins needed by your cluster |
| `DEPLOY_CI_IMAGE` | Image providing provider-specific deployment CLI for generic deploy/preview components |
| `RELEASE_CI_IMAGE` | Internal glab image compatible with your GitLab release job implementation |
| `CI_IMAGES_REPOSITORY` | Image-factory destination prefix, e.g. artifactory.internal/docker-local/ci |
| component `image`, `runner-tags`, `timeout` | Per-job image, runner and timeout overrides |
| component `destination`, `base-image`, `platform` | Full push image reference, Dockerfile BASE_IMAGE argument, target CPU architecture |
| `PREVIEW_IMAGES_REPOSITORY`, `PREVIEW_REGISTRY_AUTH_FILE`, `PREVIEW_DOMAIN` | Dedicated preview image prefix, least-privilege auth file and ingress domain |
| `REGISTRY_AUTH_FILE` | GitLab **file** variable holding Docker auth JSON for BuildKit pulls/pushes; least privilege, protected for pushes |
| `DOCKER_AUTH_CONFIG` | Runner-side image pull authentication; this is distinct from BuildKit's auth file |
| `BUILDKIT_CONFIG_FILE` | GitLab **file** variable holding buildkit.toml; registry CA/mirror settings |
| `BUILDKITD_FLAGS` | Rootless defaults supplied by the component; runner-specific customization requires a reviewed override |
| `PIP_INDEX_URL` | Internal virtual PyPI `/simple` endpoint; runtime image preparation requires it |
| `PIP_CONFIG_FILE` | Optional pip configuration file path, usually a GitLab file variable; default non-secret config is /etc/pip.conf |
| `UV_DEFAULT_INDEX` | Override uv's default index with your internal endpoint; align project uv.lock URLs with it |
| `UV_CONFIG_FILE` | Optional uv config path (non-secret /etc/uv/uv.toml is baked into the supplied Python Dockerfile) |
| `UV_INDEX_INTERNAL_USERNAME`, `UV_INDEX_INTERNAL_PASSWORD` | Credentials for the named `internal` index in the image uv.toml; scoped CI secrets |
| `UV_INDEX_URL` | Legacy alternative for older uv installations; prefer supported variables for your pinned uv release |
| `UV_PYTHON_DOWNLOADS=never` | Prevent managed Python downloads; the requested interpreter must exist in the image |
| `UV_CACHE_DIR`, `UV_LINK_MODE` | uv cache location and copy/hardlink mode; component sets cache under repository root |
| `UV_OFFLINE` | Optional strict cache-only mode; normally unset for internal Artifactory use |
| `UV_NO_BUILD_ISOLATION` | Optional when every required build backend is preinstalled; otherwise mirror build-system requirements internally |
| `UV_NO_INDEX`, `UV_FIND_LINKS` | Optional local wheelhouse-only resolution; requires complete project dependencies |
| `PIP_NO_INDEX`, `PIP_FIND_LINKS` | pip equivalents for strict local-wheel resolution |
| `SSL_CERT_FILE` | Full PEM trust bundle for Python/uv TLS |
| `REQUESTS_CA_BUNDLE`, `PIP_CERT`, `GIT_SSL_CAINFO` | Full PEM trust bundle for requests/twine, pip and Git |
| `TWINE_CERT` | Optional explicit CA path for twine uploads |
| `CI_SERVER_TLS_CA_FILE` | GitLab-provided server CA file when configured; may not include Artifactory or other corporate roots |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | If your internal network uses a proxy; set lowercase equivalents for tools that need them |
| `PYPI_UPLOAD_URL` | Artifactory local PyPI upload endpoint, e.g. https://artifactory.internal/artifactory/api/pypi/pypi-local |
| component `repository-url` | Publish endpoint; explicit input supports PyPI, TestPyPI, GitLab or Artifactory |
| `TWINE_USERNAME`, `TWINE_PASSWORD` | Environment-scoped masked credentials; Artifactory normally uses a service username plus token, not PyPI's __token__ username |
| component `auth` | `token` for Artifactory/PyPI, `gitlab` for CI_JOB_TOKEN package upload, `oidc` only when the destination actually supports your issuer |
| component `audience` | OIDC audience, e.g. pypi or testpypi; do not assume self-managed GitLab OIDC works on every registry |
| `CI_DEPENDENCY_REPO`, `CI_DEPENDENCY_REF`, `CI_DEPENDENCY_PACKAGE`, `CI_DEPENDENCY_OVERRIDES` | Candidate dependency test controls; see root README |
| Git credential helper / GitLab allowlist | Read-only access to cross-repository candidate branches; authenticate the host without embedding tokens in URLs |
| `KUBECONFIG` | GitLab file variable or GitLab Kubernetes Agent-provided kubeconfig |
| `HELM_REGISTRY_CONFIG` | Runtime Helm OCI registry auth configuration file; never tracked or cached |
| component `chart`, `chart-version` | Internal OCI chart address and exact version; a local consumer chart path also works |
| component `namespace`, `release-name`, `values-file`, `image-map-json` | Helm deployment target, configuration and digest artifact mapping |
| `CI_FULL_PIPELINE` | Full monorepo validation switch in the monorepo example |

Not every standard tool variable is re-declared as a component input: native environment variables remain available. Prefer one internal virtual Python repository over a public extra-index fallback. A lockfile may embed source registry URLs; configuring uv's default index alone does not rewrite a lock created against the public internet. Generate/review the production lock against the internal indexes and mirror all package/build dependencies.

## Trust at four layers

1. **Runner host / executor:** trusts GitLab and Artifactory before a job begins, including job image and helper image pulls. Pre-mirror GitLab Runner helper images matched to the installed runner version. Job environment variables cannot repair a failed initial image pull.
2. **BuildKit:** trusts both source and destination registries using its own CA configuration. A rootless worker needs runner support for user namespaces and mounts; validate your Docker/Kubernetes/OpenShift security policy. No privileged Docker-in-Docker daemon is assumed.
3. **Runtime image:** trusts GitLab dependency repositories and package indexes through a full CA bundle. Add public/internal roots together; do not replace a complete bundle with a single leaf certificate. The Python Dockerfile has no apt or public curl step.
4. **Kubernetes nodes:** trust the image registry before pods start, and have namespace-local imagePullSecrets. Python certificates inside the image cannot fix node image pulls.

The chart's application pods do not automatically inherit the CI image's certificates. Build application images with the required trust separately.

## Image factory

`images.gitlab-ci.yml` has an internal wheel-download preparation job, then rootless Docker validation/build jobs. The template repository can trigger it manually as a child pipeline. It can also be selected explicitly as a project's CI config file. Run with the private indexes and image variables above.

`images/python/requirements.lock` is a hash-pinned transitive tool lock generated for Python 3.12. Review all versions and mirror those wheels into Artifactory. The preparation job downloads wheels for the base image's platform with `--require-hashes`; the Dockerfile installs with `--no-index --require-hashes`. This separates internal acquisition from the completely network-independent Python-tool installation layer. The base image itself still comes from an internal registry.

To refresh the lock in an approved preparation environment:

```sh
uv pip compile images/python/requirements.in --generate-hashes --python-version 3.12 --output-file images/python/requirements.lock
```

Use your internal index during refresh. Verify wheel availability for each required architecture. Run separate preparation/build pipelines for different target architectures; a wheelhouse downloaded on amd64 does not automatically support arm64.

The Dockerfile's `BASE_IMAGE` is required and should be digest-pinned. It must already have Python, pip, Git and update-ca-certificates. Add certificate files and non-secret pip/uv configs to the build context. Never bake tokens, service account keys, `.netrc`, Docker auth or kubeconfig into an image. Supply application-specific compilers/build backends in a reviewed base or derived image when pure Python tools are insufficient.

The generic `container-build` component also supports custom Dockerfiles for Helm, glab, build tooling and application images. This bundle provides the Python runtime Dockerfile; mirrored BuildKit/Helm/glab base images are organization-supplied prerequisites, not binaries distributed by this archive.

`push: false` validates and exports an OCI archive in the job workspace; only digest metadata is retained by default to avoid large artifacts. `push: true` requires a protected ref and REGISTRY_AUTH_FILE. Use distinct metadata filenames for parallel image jobs, e.g. api-build-metadata.json. Registry tag immutability is configured in Artifactory, not guaranteed by YAML.

## Permissions and release setup

- Protect release tags and production environments in GitLab; use deployment approvals if your edition supports them. YAML rules alone are not authorization against someone allowed to edit the pipeline.
- Set Artifactory tokens with repository-scoped read/write rights and the correct username. Scope production tokens to the release environment. Review retention so artifacts remain available through approval windows.
- Pre-create namespace-scoped Kubernetes credentials and image pull secrets. Preview credentials should be limited to preview namespaces. Do not expose production credentials to MR pipelines or forks.
- Candidate dependency builds run repository code. Allowlist trusted source repositories and restrict the credentials available to those jobs. The URL validator is format validation, not a host allowlist or trust boundary.
- Public PyPI cannot be reached from a fully disconnected network. Publish internally or use an explicitly approved promotion process outside the gap.

## Target-instance CI compilation

After committing the component files and adapting an example, run the real server compiler:

```sh
# CI_API_V4_URL=https://gitlab.internal/api/v4
# GITLAB_API_TOKEN is supplied securely; do not write it to shell history.
python scripts/gitlab_lint.py .gitlab-ci.yml --project team/application --ref main
```

Repeat for an existing release tag. Use GitLab's MR pipeline testing for MR-specific rules; a branch dry-run alone is not an MR simulation. The script uses your SSL_CERT_FILE trust bundle, reads the API token only from its environment, and returns nonzero for invalid configurations.
