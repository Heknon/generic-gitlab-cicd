# Implemented workflows — revision one

The default workflow interface uses team-defined checks and event selection. Start with the [README quick start](../README.md#quick-start). Version 0.3.2 passed the repository’s fast CI and disposable GitLab/Runner E2E scenarios; Buildah, registry publication, and OpenShift rollout still require qualification on the target infrastructure.

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
- `publish: true` (or `publish: {channel: auto}`) selects release publication and automatically includes the package build. `publish: {channel: development}` selects snapshot publication on a non-release workflow; see the package publication section below. Node uses npm-compatible packing/publishing even when installation uses Bun or pnpm; npm must be installed in those role images.

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

Release publication with candidate overrides is blocked. Python candidate image builds require container.dependency-bundle: true and a Dockerfile consuming the named ci-dependencies context (wheelhouse/requirements.txt); application-specific image tests must verify consumption. Node candidate image builds are explicitly blocked until equivalent bundle parity exists. Ordinary source-only image builds never silently discard candidate overrides.

## Helm

Chart sources: local path; repository/name/version; or oci/version. Values files are repository-relative and applied in order. Remote chart archives are resolved once per job. Classic repository credentials may be supplied through TOOLKIT_HELM_USERNAME/TOOLKIT_HELM_PASSWORD; TOOLKIT_HELM_CA_FILE supplies CA. OCI authentication uses HELM_REGISTRY_CONFIG prepared by the platform. Never enable insecure TLS as an automatic fallback.

Each images entry references project.build-image and maps repository/tag/digest to explicit dotted chart values paths. Map keys, not literal addresses. Array indices and literal dots inside keys are not yet supported. Mappings apply last through a generated values file; render validation checks that the expected image appears in workload containers.

Deployments select a platform target with namespace, kubeconfig-variable, url, production and optional release name. Configure distinct targets for staging/production. MR deployments append project/MR identity and require same-project MRs. MR previews select every mapped image build to establish their own baseline. Complete deployments also select all mapped producers when affected; partial release deployments preserve unchanged images from their own existing baseline.

Partial shared-release updates preserve unchanged mapped image values from the deployed Helm release. The first install requires all mapped outputs. A fingerprint prevents partial chart/config changes; explicit complete deployment is needed. A failed/pending prior release blocks reconciliation. The GitLab resource group serializes each target/release read/merge/upgrade within one GitLab project. Cross-repository writers must use a single deployment owner; resource groups are not distributed locks.

Manual approval precedes before.checks; after.checks receive DEPLOYMENT_URL from deployment output. Post-check failure fails the pipeline but does not automatically roll back the database or mark the earlier GitLab deployment job failed. Concurrent later deployment may begin before separate after checks finish; do not use this mode for tests requiring exclusive environment ownership without additional coordination.

MR stop is manual, matches start eligibility and works without checkout/build artifacts. Helm uninstall removes chart-owned resources; external-resource cleanup and scheduled orphan reconciliation are not yet implemented.

## Verification boundary

Local tests cover schema/graph contracts, actual artifact handoff and tamper rejection, offline npm installation, Python preparation integration, SemVer ordering and partial image-map behavior. Buildah command assembly is mocked. Live GitLab CI Lint, OpenShift runner behavior, Helm upgrades/rollbacks, registry publication, full cold air-gap runs and native Bun/pnpm install matrices remain required before production adoption. These limitations are not waived by passing local tests.

## Package publication — revision four

The toolkit provides version generation and destination selection; teams own publication policy. We recommend treating development packages as explicitly selected test artifacts in a separate preview repository. Beta/RC releases can use the shared index for ordinary prerelease consumers. Sharing an index is also supported when that is the team’s intended behavior.

```yaml
projects:
  sdk:
    path: packages/sdk
    python: {groups: all}
    package:
      index: internal
      preview: {index: preview}
    checks:
      unit:
        script: ["uv run --no-sync pytest"]
    release:
      tag: "sdk-v{version}"
    workflows:
      merge-request:
        checks: [unit]
        publish: {channel: development}
      release:
        checks: [unit]
        publish: {channel: auto}
```

Package build is inferred from publication; it waits for the event's selected checks and version validation. No hidden test names are added. Additional generated inputs still use `package.needs`. A development publish job has the same public `sdk.publish` reference in its event; `release.needs` only applies to release workflows. Selecting a deployment still requires its image build to be enabled explicitly.

Define Python URLs in the package's pyproject.toml:

```toml
[[tool.uv.index]]
name = "internal"
url = "https://artifactory.internal/api/pypi/python-release/simple"
publish-url = "https://artifactory.internal/api/pypi/python-release"

[[tool.uv.index]]
name = "preview"
url = "https://artifactory.internal/api/pypi/python-preview/simple"
publish-url = "https://artifactory.internal/api/pypi/python-preview"
explicit = true
```

`explicit = true` prevents the preview index from participating in ordinary resolution unless a package is assigned to it. This is a recommended configuration, not a pipeline requirement. Shared index names and URLs are supported. Omit `package.preview` to publish development versions through the normal package destination. Keep the default/internal index configuration suitable for your network; `explicit` does not configure or disable other indexes.

For Node, use `node: {package-manager: npm}` (or pnpm/Bun), retain the normal registry in `package.json`'s `publishConfig.registry`, and declare `package.preview.registry: https://artifactory.internal/api/npm/npm-preview`. The override is optional and may use the same registry as normal publication. The temporary package's `publishConfig` is rewritten too, to match the selected development destination and `dev` tag. Publication still uses npm for pnpm/Bun projects.

| Workflow publication | Version | Destination | GitLab Release |
| --- | --- | --- | --- |
| `channel: development` | Python `1.5.0.dev4821`; Node `1.5.0-dev.4821` | Preview override, or normal destination when omitted | Never |
| `channel: auto` or `true` | Declared version, matching the protected release tag | Normal publishing destination | Yes |

Snapshot numbers use `CI_PIPELINE_ID`, stable across a job retry and unique within one GitLab instance. The release base is taken from the declared version: `1.5.0rc1` also produces `1.5.0.dev4821`. Python local/postrelease versions and dynamic versions are rejected for snapshot generation. Package ownership must be unique across repositories; sharing a preview repository across independent GitLab instances needs separate repository namespaces to prevent ID collisions.

The runtime copies the prepared repository into temporary storage, changes only that copy's static package version, builds and validates archive metadata, then deletes the copy. Original manifests and locks are unchanged, including on failure. Prepared dependencies and generated workspace files are copied too; allow temporary disk space accordingly. Git metadata is excluded: builds requiring Git-derived versions or Git commands in packaging hooks are unsupported in this mode. Internal sibling dependency constraints are not rewritten automatically. Publishing multiple workspace packages together does not make them depend on each other's snapshot versions.

Build receipts contain the exact package version, artifact hashes, pipeline/commit identity and preview destination. Publishing requires the matching receipt. Development publication generates a snapshot on non-release workflows; `auto` publishes the version matching a release tag. The toolkit does not prohibit development publication based on fork status or candidate overrides. GitLab and registry access control determines which jobs can upload. Development publication does not need a GitLab release API token. Candidate overrides affect dependency preparation; they do not automatically rewrite the published package’s dependency requirements.

For normal publication, `auto` reads the declared version; it never changes it. Python alpha/beta/RC versions stay in the shared release index. Node derives a dist-tag from the first prerelease identifier (for example `beta`, `rc`, `dev` or `canary`), using `latest` for stable versions and `next` for a numeric first identifier. An explicit archive `publishConfig.tag` takes precedence. Tagged development versions can also use normal publication; the toolkit does not restrict the team’s chosen version policy.

### Consuming a specific snapshot

For reproducible testing, we recommend selecting both the preview repository and exact version. For uv, configure the preview index as above and use:

```toml
[project]
dependencies = ["company-sdk==1.5.0.dev4821"]

[tool.uv.sources]
company-sdk = {index = "preview"}
```

Update and commit the lockfile for a persistent selection; normal CI installs remain frozen. Keep temporary experiments in a branch, or use the toolkit's existing repo/ref/package overrides when testing source without publishing. No global prerelease opt-in is needed for this exact prerelease requirement with normal uv defaults; an explicit disallow policy must be adjusted.

For npm-compatible consumers, configure a scope-specific preview registry in the test branch's npm configuration and pin the exact snapshot version. A scope mapping affects every package in that scope; if stable siblings need another registry, use a deliberately configured test-only virtual registry or another explicit package source. Do not point normal consumers at that test registry. The preview `dev` dist-tag is mutable across branches and is not an exact-build selector.

### Recommended Artifactory setup

- To keep branch packages out of normal dependency upgrades, provision separate release and preview repositories and exclude previews from normal virtual repository aggregation. This is an organizational choice; the toolkit does not enforce separation or inspect Artifactory membership or aliases.
- Prefer preview-scoped credentials for branch/MR jobs and protected release credentials for release jobs. Use index-specific uv credentials and registry-scoped npm authentication. Manage credentials through your platform’s secret configuration.
- Add both hosts to platform `allowed-hosts`, install the appropriate CA trust in role images, and provide internal build dependencies. No public bootstrap is introduced.
- Configure immutable versions/no overwrites and a preview retention policy. Retain packages needed by active test locks; deleting a pinned snapshot breaks clean installs.
- A retry reuses the version. uv uses the explicit destination's check URL for duplicate checking. npm existing-version retries fail; reconcile partial publication before retrying. The toolkit never deletes or overwrites packages to make a retry succeed, and does not yet provide cross-registry promotion or an npm remote-checksum reconciliation adapter.

Local compilation, archive tests and mocked upload commands do not prove live registry permissions or isolation. Validate an intended workflow publication and exact-version install against your actual Artifactory/GitLab setup before adoption.

## Shared OpenShift chart — revision five

The bundled `charts/generic-app` chart is version **2.0.0**. Each `apps` entry deploys a stateless workload with optional Service and OpenShift Route. Images use `image.repository` and `image.tag`; map build output with `repository: apps.api.image.repository` and `tag: apps.api.image.tag`. The complete values example is `examples/helm-values.yaml`; the workflow deployment example is `examples/workflows/delivery.yml`.

Routes accept `route.enabled`, `host`, `path`, `annotations`, `wildcardPolicy` and a `tls` mapping. TLS uses OpenShift field names: `termination`, `insecureEdgeTerminationPolicy`, `certificate`, `key` (private key), `caCertificate`, and `destinationCACertificate`. Edge termination uses the client-facing certificate/key/CA chain; re-encryption also supports a backend CA. Passthrough leaves TLS in the application. Omitting TLS creates an unsecured Route. The chart forwards these settings; it does not inspect certificates or enforce certificate policy.

Supply PEM strings through values, or with `helm --set-file` when invoking Helm directly. Workflow deployment supports its existing ordered values files; no new `set-file` workflow field is introduced. Real keys belong in the team's secret-delivery mechanism. Route keys appear in rendered manifests and Helm release data, so account for those in artifact/access configuration. The chart does not issue or rotate certificates.

This replaces chart 1.x Ingress/digest values. Migrate old values and bindings when upgrading; older digest-only component adapters remain compatible with chart 1.x. Buildah continues recording digests as build evidence. Shared repository availability requires publishing the chart separately; the illustrative repository URL is not a claim that it has been uploaded.


## Chart configuration flexibility — revision six

Chart **2.1.0** adds per-app native `volumes`, `volumeMounts`, `ports`, `podLabels`, `podAnnotations`, `podSecurityContext`, `securityContext`, and `automountServiceAccountToken`. Services support native `ports`, `labels`, and `annotations`; `route.targetPort` selects the Service port and defaults to `http`. Container ports describe app listeners, Service ports map cluster access, and Routes expose one selected port. A metrics port can remain internal while HTTP is routed externally.

Security contexts and token mounting are unset by default, leaving the image/service-account/cluster defaults in effect. No automatic `/tmp` volume is injected. Teams wanting the earlier restrictions and temporary mount can configure them explicitly. OpenShift admission policy still applies. Probes and resources are optional; requests and limits may be set independently. These are capabilities and configuration defaults, not enforced organizational policy.

The original single-port `service.port`/`service.targetPort` shorthand remains supported. Explicit port lists replace the shorthand. Lists in additional Helm values files replace earlier lists. Volumes reference existing resources or use native ephemeral sources; the chart does not automatically provision PVCs. Monitoring annotations require a corresponding discovery configuration; ServiceMonitor/PodMonitor creation is not included. No init containers or sidecars are added in this revision.
