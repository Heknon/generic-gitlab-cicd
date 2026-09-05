# Implemented workflows — revision one

The workflow interface is available in the 0.3.0 feature branch. It replaces hidden test presets with team-defined checks and event selection. It remains a review build: target GitLab, Buildah/OpenShift and registry/cluster integration have not been exercised in this workspace.

## Start and inspect

Install the versioned toolkit wheel and dependencies from your internal package service or image factory. The same version must be installed in every runtime image. No component downloads this repository into consumers.

```
generic-ci init --ecosystem python --config delivery.yml
generic-ci validate --config delivery.yml --platform ci-platform.yml
generic-ci explain --config delivery.yml --platform ci-platform.yml
generic-ci render --config delivery.yml --platform ci-platform.yml -o .gitlab-ci.yml
```

Commit authoring files and generated YAML. The plan job verifies source fingerprints. `render --check -o .gitlab-ci.yml` detects drift locally. The current explain output is structured JSON containing effective execution fields, workflow selections and job dependencies; a friendly event simulation UI is not implemented.

Use `generic-ci schema` and `generic-ci schema --platform-schema` to export editor schemas. Bundled copies are schemas/workflows.schema.json and schemas/workflows-platform.schema.json. Point your editor at the local schema for offline completion. Existing prototype configuration remains accessible with `--format legacy`. Existing component includes remain unchanged.

## Projects, checks and operations

See examples/workflows for compilable configuration examples. Their source directories and internal images are illustrative and must be supplied by the consumer; compilation is not an application execution test.

- `python: {}` uses uv and project defaults. `groups: all` includes all groups, `extras: all` includes optional extras separately. Explicit group lists replace default groups. Workspaces use the owning lockfile and selected member.
- `node.package-manager` selects npm, pnpm or Bun. Frozen installs require a committed corresponding lockfile. Bun text bun.lock is required. Workspace membership is discovered or explicitly provided through `node.workspace`.
- `checks` are arbitrary team names and commands. No hidden unit/lint suite is inserted.
- `workflows` supports push, merge-request, release, manual (GitLab web pipeline), and schedule. Checks are explicit lists. Push is suppressed when an MR pipeline takes its place; lists are not implicitly combined.
- `build.script` and `build.outputs` define application compilation, code generation or static frontend output. `container` defines Buildah input. `package` defines an ecosystem package build.
- Workflow `build: true` requires exactly one configured output. With multiple outputs use `build: [application, container, package]`, selecting only the operations needed.
- `publish: true` is available only in a release workflow with package build selected. Node uses npm-compatible packing/publishing even when installation uses Bun or pnpm; npm must be installed in those role images.

React needs no dedicated runtime abstraction: define type checking/testing commands and an application build producing dist (or your framework's actual directory). Static output is exposed as artifacts, not automatically published to a CDN. Server-rendered frontends and Node backends can declare container builds.

## Three dependency relationships

1. Project `depends-on: [sdk]` retests the consumer when sdk changes. It never installs an unreleased SDK implicitly. Direct source changes and transitive consumer selection are recorded separately; retesting alone does not force a consumer version bump.
2. Check/build `needs: [sdk.build]` requires same-event application artifacts. Public references are project.check-name, project.build, project.build-image, project.build-package. The producer must be enabled in that event. Receipts verify config, commit, pipeline and file checksums; missing, failed or modified evidence blocks consumers.
3. `release.needs: [sdk]` orders publication. Currently both projects must use the same coordinated tag convention. Independent cross-repository publication orchestration is not implemented; use candidate validation before coordinated releases or publish the prerequisite first.

Change selection is conservative for root workspace manifests/locks and missing shallow baselines. Project watch patterns include shared source paths. A shared generated SDK and API may use separate project identities with the same source directory; directory containment is not project identity.

## Custom execution

Platform defaults merge with project defaults and check fields. Scalars replace, maps merge recursively and lists replace. Omitted fields inherit. There is no null-based removal operator. Checks may set dependencies: false for no environment preparation; Python preparation settings may be overridden per check. Commands use sh in this first implementation; Windows workflow execution is not supported yet.

Matrix combinations become explicit jobs and all selected instances gate consumers. Shared artifact outputs from matrix checks are rejected. Configure unique report paths using matrix variables; custom report paths are not rewritten automatically. Commands run from the project directory; native GitLab artifact paths are repository-relative. Artifact paths under .ci-out are owned by the toolkit.

## Version bumps and release button

Python defaults to pyproject.toml project.version; Node defaults to package.json version and SemVer ordering. Explicit file/field selection is supported. A release declaration inserts MR version validation even if no other MR checks are selected. Directly changed projects must increase their version over the fetched target branch and existing matching release tags. New version files are permitted only when absent in the fetched baseline. Dynamic Python version providers are not supported.

`release.create: {branch: main}` requires push and release workflows and creates a manual release job after push checks. The configured branch must be protected. Provide TOOLKIT_RELEASE_TOKEN as a protected API-capable project access token. The job creates a tag at CI_COMMIT_SHA via GitLab's Tags API. It does not move existing tags: same commit is idempotent, another commit is an error. Verify tag-pipeline triggering on your GitLab version/token configuration before adoption.

Release workflows validate the tag, gate builds on checks, and finalize the GitLab Release after required builds/publication. The release entry uses the same token. npm publication retries are not yet idempotent for an existing version; reconcile a partial publication before retrying. Enforce successful pipelines in repository merge settings.

## Buildah and air gap

Provide images.python, images.node, images.bun, images.helm and optionally images.control. Required role images are checked; there is no public fallback. Every image needs Python 3.11+ and this exact toolkit version because the runtime handles receipts and dispatch. Node/Bun images also need npm and Git. The builder image needs Buildah and Python; Helm image needs Helm, Git and Python. Runtime acquisition must be prepared offline, not installed in each consumer pipeline.

`container-builder` selects Buildah image and runner tags. Push authentication uses Buildah's supported REGISTRY_AUTH_FILE; supply it as a file-type variable. Registry trust and storage/isolation settings belong to the prepared builder/runner. Container versions invalid as tags are rejected, not silently transformed. CI images use pipeline/commit tags in the preview repository; release images require protected tags and use the declared version. Registry digest comes from buildah push --digestfile.

The image factory now builds Python, Node, Bun, Helm and Buildah runtime roles from supplied internal bases. Every base must already contain its role tools plus Python/Git/certificate-update support. Supply PYTHON_BASE_IMAGE, NODE_BASE_IMAGE, BUN_BASE_IMAGE, HELM_BASE_IMAGE, BUILDAH_BASE_IMAGE, BUILDAH_FACTORY_IMAGE and CI_IMAGES_REPOSITORY. The shared Dockerfile adds the offline toolkit wheelhouse and trust configuration. The initial factory image is bootstrapped by the platform administrator. No credentials are baked in.

Set internal package indexes in pyproject/uv configuration or npm configuration; Node runtime requires an internal default registry. Publishing uses the named uv index or package.json publishConfig.registry. Internal CA setup is independent at runner pull, runtime, builder and Kubernetes pull boundaries. Scope-specific npm registries, lockfile URLs and lifecycle scripts also need network enforcement; URL validation alone cannot prove a cold air-gap run succeeds.

## Candidate dependencies

CI_DEPENDENCY_REPO, CI_DEPENDENCY_REF and CI_DEPENDENCY_PACKAGE select a single candidate. CI_DEPENDENCY_OVERRIDES accepts a JSON array with repository/ref/package/subdirectory/projects. CI_DEPENDENCY_FILE selects an equivalent JSON file. Input sources are exclusive; branches are resolved once and the immutable commit is recorded.

Python candidates use resolver overrides, verify installed direct_url identity and restore manifests/lock on failure. Node candidates clone the exact commit, pack existing distributable content without scripts, apply temporary root overrides and consumer dependency entries, regenerate a temporary lock, and verify installed files against the candidate tarball. Node candidates must already contain distributable output and expose an installed package at node_modules; complex peer/override combinations may fail and are not silently bypassed. Existing pnpm-workspace.yaml overrides currently require manual reconciliation. Node candidate adapter needs live pnpm/Bun integration validation.

Candidate release publication is blocked. Python candidate image builds require container.dependency-bundle: true and a Dockerfile consuming the named ci-dependencies context (wheelhouse/requirements.txt); application-specific image tests must verify consumption. Node candidate image builds are explicitly blocked until equivalent bundle parity exists. Ordinary source-only image builds never silently discard candidate overrides.

## Helm

Chart sources: local path; repository/name/version; or oci/version. Values files are repository-relative and applied in order. Remote chart archives are resolved once per job. Classic repository credentials may be supplied through TOOLKIT_HELM_USERNAME/TOOLKIT_HELM_PASSWORD; TOOLKIT_HELM_CA_FILE supplies CA. OCI authentication uses HELM_REGISTRY_CONFIG prepared by the platform. Never enable insecure TLS as an automatic fallback.

Each images entry references project.build-image and maps repository/tag/digest to explicit dotted chart values paths. Map keys, not literal addresses. Array indices and literal dots inside keys are not yet supported. Mappings apply last through a generated values file; render validation checks that the expected image appears in workload containers.

Deployments select a platform target with namespace, kubeconfig-variable, url, production and optional release name. Configure distinct targets for staging/production. MR deployments append project/MR identity and require same-project MRs. MR previews select every mapped image build to establish their own baseline. Complete deployments also select all mapped producers when affected; partial release deployments preserve unchanged images from their own existing baseline.

Partial shared-release updates preserve unchanged mapped image values from the deployed Helm release. The first install requires all mapped outputs. A fingerprint prevents partial chart/config changes; explicit complete deployment is needed. A failed/pending prior release blocks reconciliation. The GitLab resource group serializes each target/release read/merge/upgrade within one GitLab project. Cross-repository writers must use a single deployment owner; resource groups are not distributed locks.

Manual approval precedes before.checks; after.checks receive DEPLOYMENT_URL from deployment output. Post-check failure fails the pipeline but does not automatically roll back the database or mark the earlier GitLab deployment job failed. Concurrent later deployment may begin before separate after checks finish; do not use this mode for tests requiring exclusive environment ownership without additional coordination.

MR stop is manual, matches start eligibility and works without checkout/build artifacts. Helm uninstall removes chart-owned resources; external-resource cleanup and scheduled orphan reconciliation are not yet implemented.

## Verification boundary

Local tests cover schema/graph contracts, actual artifact handoff and tamper rejection, offline npm installation, Python preparation integration, SemVer ordering and partial image-map behavior. Buildah command assembly is mocked. Live GitLab CI Lint, OpenShift runner behavior, Helm upgrades/rollbacks, registry publication, full cold air-gap runs and native Bun/pnpm install matrices remain required before production adoption. These limitations are not waived by passing local tests.
