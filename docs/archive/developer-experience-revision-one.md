# Developer experience — revision one

Status: reviewable design, not an implemented or released interface.

This revision supersedes the authoring recommendations in product-contracts-revision-one.md and the delivery-tool comparison in implementation-checkpoint-revision-one.md. Existing components and the compiler prototype remain available for evaluation; they do not yet implement this contract. No production-readiness claim is made.

## Purpose and ownership

Teams define checks and select when they run. The toolkit prepares environments and assembles GitLab pipelines. It provides package publishing, image building, and Helm deployment without requiring ordinary users to write GitLab rules or understand generated job names.

- Projects identify source locations and buildable/publishable units.
- Checks are user-named commands with execution settings. No check name has special meaning.
- Workflows select checks and operations for an event.
- Deployments connect image build outputs to explicit Helm values paths.
- Platform configuration supplies internal images, runners, endpoints and secret references.

Initialization writes visible example checks; presets must not introduce hidden mandatory unit/lint/integration suites. Platform-mandated checks, if introduced, must be separately identified as policy, not overridable defaults.

## Authoring files

See ../examples/design-revision-one/delivery.yaml and platform.yaml. These are proposed input documents, deliberately separate from examples/product, which exercises the previous prototype. Do not run the existing compiler against the new documents.

A minimal project needs a path, Python preparation if desired, named checks, and workflow check selection. Container and package operations are opt-in. Deployment objects live at repository scope and may bind one or many projects.

## Checks and inheritance

A check has script, and may supply image, tags, timeout, variables, services, parallel, artifacts, and dependencies. Familiar executor fields keep their documented GitLab meaning where supported; unsupported fields fail validation. They do not permit replacing toolkit-owned dependency wiring.

Precedence: toolkit defaults, platform defaults, project defaults, check settings. Omitted fields inherit; scalars replace; mappings merge recursively; lists replace. Empty mappings do not erase inherited entries. Field removal is an unresolved syntax decision and must be settled before schema freeze; arbitrary null must not silently delete required preparation.

A script override replaces the user command only. Preparation remains a separate toolkit operation. Report paths remain visible and may need adjustment when commands change. Do not infer arbitrary command behavior.

A fully custom check may disable Python preparation explicitly. Windows shell runners and Linux container runners need distinct tested runtime contracts; changing tags does not change shell syntax.

## Python preparation

Use the project's pyproject.toml and uv.lock, including workspace membership. With no CI overrides, respect uv default groups and sync with lock validation. Do not redefine package requirements in this file.

- groups: all installs every dependency group.
- groups: [test] replaces default group selection and installs that group plus ordinary project dependencies.
- extras: all independently enables all optional extras.
- upgrade: none is the default; normal runs validate and use the committed lock.
- upgrade: all requests latest versions satisfying constraints for that check invocation.

All groups does not mean all workspace members. Conflicting groups/extras fail with an actionable error. Upgrade jobs save their resolution evidence and restore tracked files even after failure; their environment must not silently become release input.

Candidate dependencies are per-run inputs, supplied via manual input, an environment variable, or a selected file. Entries identify repository, ref, distribution name, optional subdirectory, and selected consuming projects. Reject conflicting input sources. Resolve refs once to immutable commits, verify installed identity, and record the resolution. Credentials remain external. Apply substitutions as resolver inputs, not an unverified pip overlay after sync. Never allow candidate-derived artifacts into normal release publication. A candidate preview must actually contain the candidate dependencies it tested; Dockerfile integration must be verified.

## Workflow events

The initial vocabulary is push, merge-request, and release. Manual and scheduled entrypoints need explicit contracts before implementation; they must not accidentally inherit publishing behavior.

- push handles branch pushes without an open MR.
- merge-request handles MR creation and subsequent source updates. It replaces the push workflow when an MR exists; lists are not implicitly unioned.
- release handles tags matching the project's release.tag and validates metadata against the extracted version.

Each workflow selects checks. All selected checks gate its requested build or publish operations. A failed check blocks those operations. A matrix check requires all selected instances. Unknown references and cycles fail validation.

For container projects build: true means build that project's image. For package projects it means build distributions. A project producing both needs explicit operation selection: this syntax must be resolved before implementation; do not guess. publish: true is restricted to package release workflows. Container release builds publish their image; non-release build/push behavior must distinguish preview and release registries.

No workflow means no implied event execution. No checks means no user checks, but artifact identity and version consistency validation still apply.

Affected-project selection and event selection are separate. Shared files, workspace locks, and explicit affected-by project relationships propagate selection conservatively. affected-by never substitutes packages, consumes artifacts, or orders publication.

## Buildah and build artifacts

Buildah is the default engine. The platform provides its approved image and suitable runner tags. No public download bootstrap. Validate actual OpenShift runner requirements rather than promising all restricted runners work.

An image build emits a versioned artifact containing repository, tag, digest, source commit, pipeline/job identity and candidate status. Consumers verify it came from the expected successful producer. Tags need not equal package versions in preview builds. The toolkit must not infer an output from the latest registry tag.

Build contexts and Dockerfiles are project-relative; explicitly repository-relative forms are needed for shared files. Build secrets use temporary secret mounts, not image layers or ordinary build arguments. Application Dockerfile base images are independent of the CI executor image.

## Helm deployment

Exactly one chart source:

- path: repository-relative local chart.
- repository + name + version: classic Helm repository.
- oci + version: OCI chart reference.

Remote versions are pinned. Auth and trust are supplied through platform secret references. Chart dependencies must be internally available or vendored; a repository URL alone does not establish air-gap compatibility.

values is an ordered list of repository-relative files. Later files override earlier according to the supported Helm version. Image mappings apply last. Missing files and conflicting image destination mappings fail validation. Escaping of dotted keys and indexed paths must be specified and tested before schema freeze; never interpolate mapping text as shell code.

Each images entry references a public producer such as api.build-image. set.repository, set.tag and optional set.digest are destination Helm paths. Values come exclusively from the verified build artifact. These references generate required artifact handoffs.

A custom chart is responsible for consuming the configured values correctly. Setting an unused path is possible in Helm; render validation and chart-specific fixtures must detect incorrect bindings. Tag-only charts are supported with an explicit weaker immutability guarantee than digest-consuming charts.

Deployment workflows select release or merge-request behavior and automatic/manual execution. A release invocation is eligible only when a bound project released, not on every repository tag. Permissions and protected environments remain GitLab controls; friendly configuration is not authorization.

before.checks execute freshly before mutation; after.checks execute against the resulting environment with DEPLOYMENT_URL. Checks are reusable definitions, not reusable old success results. Their working directory and source revision are explicit in execution evidence. A failed post-check reports deployment failure; automatic rollback is not implied.

## Partial shared-chart updates

Complete mode requires every mapped image. Partial mode updates only intentionally selected producers and preserves unchanged mapped image identities from the latest successful Helm release. A failed, cancelled, or unexpectedly missing selected producer blocks deployment.

Initial installation requires all mapped images or explicit baseline values. Never guess latest. Serialize the entire read/merge/deploy operation per cluster, namespace and Helm release. Reject stale attempts that would undo newer service updates unless an explicit rollback is requested.

Do not blindly reuse all old values. Read the previous successful release, preserve unchanged mapped image fields, apply the declared configuration and new image outputs with a documented precedence. A partial image-only update must reject a changed chart/configuration baseline; full configuration upgrades are separate. Helm operates on the whole release, and hooks/shared resources can affect multiple services.

No additional state repository is mandatory. If the last release is failed or pending, stop for reconciliation instead of treating it as a valid baseline. Test concurrent promotions and failed-upgrade recovery against real Helm.

## Preview deployments

MR previews have isolated environment/release identities and a documented baseline for unchanged monorepo services. A preview must never mutate a persistent release. Fork/untrusted MR execution must not receive production credentials.

Cleanup needs matching selection, serialization, manual stop, expiration, and an eventual reconciliation path. Stop operations must work after branch deletion and without build artifacts. Namespaces, external resources and retained data require explicit ownership; uninstall is not proof all resources are gone.

## Air-gap platform

Provide role-specific images for Python, Buildah and Helm; defaults for runner tags; internal release/preview registries; and documented secret/trust references. Per-check image overrides and matrices remain supported.

Cover bootstrap separately: GitLab includes/components must be internally mirrored, runners must trust and authenticate image pulls, the job runtime must trust package/Git/chart endpoints, Buildah must trust build/push registries, and Kubernetes must pull application images. Never bake credentials into images. CA bundles and nonsecret package-manager configuration may be baked into approved images.

Ship toolkit wheels, dependencies, schemas, docs, and AI authoring guidance for offline use. Disable unsolicited interpreter downloads. Validation cannot prove arbitrary scripts have no internet dependency; offline integration tests and network controls provide that evidence.

## Developer tooling, schemas, docs and AI

Proposed commands: init, validate, explain, render. Render creates reviewable top-level GitLab YAML; a drift check prevents stale generated configuration. Preserve original pipeline event semantics.

Pydantic models generate versioned JSON Schema and field reference documentation. Semantic validation checks references, matrices, event/operation compatibility and graph consistency. Editor completion supports fixed event names and arbitrary user check names; optional derived completion can suggest local references without becoming the source of truth.

Explain shows effective checks, commands, images, runners, dependency preparation, event selections, artifact handoffs and deployment prerequisites with provenance. Unknown context is reported honestly. Target GitLab CI Lint is a separate required verification; local schema validation is not GitLab validation.

An AI skill instructs agents to inspect the installed version, consult the same schema/reference, edit source documents, validate and inspect generated changes. Future MCP operations call the same CLI library. Neither is a separate implementation of pipeline behavior.

## Acceptance before implementation is called complete

| Scenario | Required evidence |
| --- | --- |
| Minimal Python checks | User-defined names, no hidden suite, locked environment |
| All groups / extras | Real uv install including conflict failure |
| Custom runner / Python matrix | Correct image/tags and isolated reports |
| Push then MR update | One intended pipeline, correct check list |
| Release | Matching project tag, failed check blocks publication |
| Candidate dependency | Immutable identity, restoration, candidate preview parity |
| Shared chart partial update | Only selected image changes; unchanged identity retained |
| Helm repository / OCI | Authenticated internal acquisition and ordered values |
| Concurrent service release | No lost update or stale overwrite |
| Preview cleanup | Works after branch deletion and expired artifacts |
| API plus generated SDK | Explicit generation artifact handoff and independent package release |
| Disconnected environment | Cold runner succeeds using internal dependencies only |

This document and examples are a first design revision. Unresolved syntax above and live verification remain gates. Existing prototype tests do not certify this revised interface.
