# Generic CI scenario audit — revision one

Date: 2026-09-05. Audited remote master: `63388e5ff294a92953be256f7b6760c779fd88b5`; local content tree: `70053f651118a8f51c9c8f47cb885388f860b586`, matching the published baseline.

## Verdict

The reusable components are a useful foundation. The proposed YAML/Pydantic project interface is not implemented and has not yet earned a claim of simplicity. The audit identifies 48 scenarios: 6 with narrow local verification, 28 partially supported, and 14 gaps. These include ordinary delivery, adverse outcomes, and the requirements raised in the design discussion. Every scenario below was checked against source and/or authoritative documentation. Only the specifically listed local checks were executed; these are not 48 passing integration tests.

Proceed with YAML backed by a shared validated model, but do not freeze the public schema until the difficult composition cases below have complete, reviewable fixtures. In particular, dependency preparation, custom-chart artifact binding, generated-pipeline event semantics, and release identity require design work. Python authoring would not remove those problems.

No production implementation was changed by this audit. No GitLab runner, cluster, registry, or publication endpoint was exercised.

## Method and evidence standard

For each scenario, check: (1) how the developer expresses it, (2) selection and ordering, (3) artifact/runtime identity, (4) failure/retry/cleanup behavior, and (5) whether support needs duplicated wiring or an undocumented override.

Coverage labels describe the CURRENT baseline:

- **Local**: the narrow behavior has executable local evidence. This is not infrastructure certification.
- **Partial**: related tooling exists, but the complete scenario needs manual assembly or has material gaps.
- **Gap**: absent or contradicted by the implementation. A custom shell command alone does not count as built-in support.

Evidence identifiers:

| ID | Evidence inspected or executed |
|---|---|
| E1 | `templates/workflow.yml`, `templates/task.yml`, all consumer examples; local example expansion tests. |
| E2 | `templates/uv-test.yml`, `scripts/uv_prepare.py`, `tests/integration_uv.py`, override unittests. |
| E3 | `templates/version-check.yml`, `scripts/version_check.py`, version unittests. |
| E4 | `templates/python-package.yml`, `templates/python-publish.yml`, `templates/release.yml`. |
| E5 | Both container build components, `images.gitlab-ci.yml`, `images/python/Dockerfile`, internal pip/uv config and tool lock. |
| E6 | Generic and Helm deployment/preview components, `scripts/image_values.py`, chart schema/templates, Kubernetes documentation. |
| E7 | `docs/airgap.md`, `AGENTS.md`, `VALIDATION.md`, `scripts/gitlab_lint.py`. |
| E8 | Audit probes and measurements in `ci-scenario-audit-evidence-revision-one.json`; detailed reproduction notes below. |
| S1–S8 | Official sources linked in the source register below. |

## Scenario checklist

Each row includes an adverse case and the condition for claiming complete support. The proposed generator has no executable coverage yet, including for rows labeled Local.

### A. Pipeline selection and execution

| ID | Scenario | Coverage and evidence | Adverse case / acceptance condition |
|---|---|---|---|
| A1 | Lint, formatting and unit tests on push/MR | Partial — E1 provides task commands, reports and default rules. | A failed check must block the intended merge/release; demonstrate on target GitLab, not merely YAML expansion. |
| A2 | Avoid duplicate push and MR pipelines | Partial — E1 has push suppression when an MR is open. | Preserve manual, scheduled and triggered runs; verify push before/after opening an MR and after closing it. |
| A3 | Manual, scheduled, API and tag runs | Partial — branch/tag-based fallback exists in E1. | Explicitly test event/ref combinations; a branch variable alone must not accidentally select production actions. |
| A4 | Python 3.11/3.13/3.14 matrix | Partial — image inputs exist; native GitLab matrices possible, no complete matrix fixture, E1/E2/S6. | Each job verifies its interpreter; distinct reports and compatible dependency resolutions; one required variant failing blocks its consumer. |
| A5 | GPU/network-specific runners; Linux/Windows/macOS | Partial — runner tags exist; uv scripts assume Unix heredocs and `.venv/bin/python`, E1/E2/S5. | Unsupported shell/image combinations fail early; tags alone cannot make Unix scripts run in PowerShell. |
| A6 | PostgreSQL/Redis services, browser tests, sharding | Partial — possible through manually extending native jobs, E1/S5/S6. | Service readiness, shard-specific output and complete result collection must be demonstrated; expensive jobs need explicit selection. |

### B. Python dependency preparation

| ID | Scenario | Coverage and evidence | Adverse case / acceptance condition |
|---|---|---|---|
| B1 | Normal committed uv lock | Local — E2 tests exact `uv sync --locked --all-groups` invocation. | Stale/missing lock fails, and the selected interpreter/group policy is explicit. CLI invocation test is not a full project compatibility test. |
| B2 | Upgrade all or selected packages for compatibility tests | Gap — E2 offers no managed upgrade policy or resolved-lock artifact. | Preserve project files, respect constraints, record the result, and keep compatibility-only resolution separate from deployable resolution. |
| B3 | Runtime repo/ref/package override | Local — real uv local-Git integration installs candidate 1.0 despite an unavailable >=2 requirement, verifies SHA; E2. | Manual-variable bridge exists in `examples/uv-airgap.yml`; file input and consistent conflicting-input validation still need implementation. |
| B4 | Multiple candidates; branch changes during matrix run | Partial — JSON list and duplicate-name rejection exist, but each job resolves refs separately, E2. | Pin all candidates once per pipeline and reuse the records; moving branch cannot cause variants to test different commits. |
| B5 | uv workspace, root lock, nested package | Gap — E8 reproduced helper failure from a valid workspace member; E2/S4. | Discover workspace root, select member deliberately, and apply overrides at the correct root without modifying unrelated members. |
| B6 | Groups/extras, private transitive deps, failed preparation | Partial — all-groups only, no selected extras interface; restoration on sync failure tested, E2. | Support explicit selection; unknown override package and auth failure must fail clearly; no hidden resync may discard the override. |

### C. Outputs and package releases

| ID | Scenario | Coverage and evidence | Adverse case / acceptance condition |
|---|---|---|---|
| C1 | Build wheel/sdist, validate, publish the built files | Partial — separate build/publish artifacts exist, E4. | Test installation of built wheel, not just source tree; publish identity must match tested artifacts. Actual upload not executed. |
| C2 | Read publish destination from pyproject | Gap — current publisher is Twine with explicit URL, defaulting to public PyPI; E4/S7. | Select uv index by name, validate `publish-url`, keep secrets outside metadata, reject ambiguous destination; airgap must reject public fallback. |
| C3 | Static version bump and tag match | Local — PEP 440 parsing/tag tests pass, E3. | Missing/invalid base version fails; branch fetching/bump comparison still needs an actual GitLab scenario and changed-project policy. |
| C4 | Dynamic/SCM versions, prereleases, independent monorepo tags | Partial — dynamic/local versions deliberately rejected; prefixes/custom rules possible, E3/E4. | Define supported strategies explicitly; docs-only change should not necessarily require every project's version bump. |
| C5 | API exports specification and builds SDK in same directory | Gap — can hand-wire tasks, but no named output/generation/release ownership contract; E1/E4. | Generated spec must feed SDK build without artifact overwrite; API and SDK may version/release independently despite shared path. |
| C6 | Coordinated cross-repo publish, retries and GitLab releases | Partial — single protected-tag release exists; no coordination/receipt protocol, E4. | If package A publishes and B fails, resume safely without pretending atomicity; release completion reflects required publications, including package-only releases. |

### D. Container builds and air-gapped infrastructure

| ID | Scenario | Coverage and evidence | Adverse case / acceptance condition |
|---|---|---|---|
| D1 | Dockerfile below project path, repository-root context | Partial — distinct root-relative context/Dockerfile inputs exist, E5. | New project-relative convention must resolve unambiguously; include shared build inputs in affected-project selection. |
| D2 | Build once, use image digest for deployment | Local — E6/E8 render digest-pinned workload images and reject bad digests. | Actual BuildKit push/pull and tested-image equivalence remain unverified; do not call rendering a successful deployment. |
| D3 | Candidate dependency inside preview container | Gap — uv test environment is not passed into Docker builds, E2/E5. | Build consumes the same candidate resolution as the tests; a fresh independent Dockerfile resolution is not equivalent. |
| D4 | amd64/arm64 builds, stages, build secrets and cache | Partial — platform input exists; only BASE_IMAGE build argument exposed, E5. | Architecture-specific wheelhouse, supported builders, secret mounts and final manifest digest require explicit handling; no credential build args. |
| D5 | Custom Python/uv/certificate CI image factory | Partial — internal acquisition and offline hashed installation are implemented, E5. | Exercise cold build per Python/architecture; factory currently documents 3.12+ base/tool lock and does not certify the requested full version matrix. |
| D6 | Standalone GitLab/Artifactory with zero public egress | Partial — internal defaults documented; publisher still defaults public, E4/E5/E7. | Cold-cache execution with public egress denied; runner pulls, Git, package/build backends, BuildKit, Helm and cluster pulls each need trust/auth. |

### E. Deployment checks and lifecycle

| ID | Scenario | Coverage and evidence | Adverse case / acceptance condition |
|---|---|---|---|
| E1 | Staging automatic, production manual, different check sets | Partial — stages/rules/manual jobs exist, no first-class per-deployment check binding, E1/E6. | A project's optional integration check should not globally block unrelated deployments merely because it occupies an earlier stage. |
| E2 | Required predeploy check skipped, canceled or allowed to fail | Gap — no semantic validator for required checks, E1/E6. | A required unavailable check cannot count as passed; validate rule mismatch and enforce runtime result, including every required matrix entry. |
| E3 | Tests against built image or temporary environment | Partial — manually assembled task/deploy components possible, E1/E5/E6. | Test subject must identify image/environment; source-tree tests alone cannot certify the deployed container. |
| E4 | Approval delay, stale environment check, expired artifact | Gap — artifacts have fixed retention, no check-freshness/promotion record, E4/E6. | Reuse immutable artifact evidence only when identities match; rerun environment-sensitive checks after delay; fail explicitly if approved artifact expired. |
| E5 | DB migration before rollout, compatibility, postdeploy smoke | Gap — custom commands possible, no lifecycle contract, E6. | Migration failure blocks rollout; smoke failure marks deployment outcome; rolling back Kubernetes resources does not undo a DB migration. |
| E6 | Concurrent deploys, old pipeline, retry and rollback | Partial — resource groups and Helm rollback flags exist, E6/S3. | Serialization alone does not prevent stale deployment; define outdated-job policy, idempotency, rollback artifact and cross-repo coordination. |

### F. Kubernetes, previews and alternate destinations

| ID | Scenario | Coverage and evidence | Adverse case / acceptance condition |
|---|---|---|---|
| F1 | API + worker, service, probes, existing TLS secret | Local — six chart objects rendered/linted, E6/E8. | Live admission, readiness, registry pulls and externally provisioned TLS remain unverified. |
| F2 | OpenShift Routes, custom chart, certificate provisioning | Partial — arbitrary chart reference accepted; image overlay hard-codes `apps.<name>.image`, E6. | Define artifact-to-chart-values binding; Route/TLS ownership and SCC admission must be tested. Merely accepting a chart URL is insufficient. |
| F3 | Stateful apps, CronJobs, volumes, sidecars, autoscaling | Gap in generic chart — E6 only renders Deployment/ConfigMap/Service/Ingress with tmp volume. | Support via custom chart without losing check/artifact/cleanup wiring; avoid recreating Kubernetes schema in CI YAML. |
| F4 | MR preview start and teardown after branch deletion | Local — manual stop/selection/no-checkout behavior structurally tested; Helm cleanup inspected, E6. | Actual expiry/stop execution still unverified; namespace and external secrets remain, so do not promise complete resource cleanup. |
| F5 | Fork MR preview, failure during setup, leaked resources | Partial — same-project restrictions exist; preview failure allowed by default, E6. | No production credentials for candidate code; explicit preview gating; cleanup ownership and reconciliation for partially created resources. |
| F6 | GitOps, canary/blue-green, VM/static site, Terraform | Partial — generic deploy commands can invoke external tooling, E6. | Treat as documented adapters/custom jobs, not built-ins. GitOps must observe reconciliation; Terraform needs saved plan, state locking and approval of that plan. |

### G. Monorepo selection and larger workflows

| ID | Scenario | Coverage and evidence | Adverse case / acceptance condition |
|---|---|---|---|
| G1 | Multiple independent projects with changed-only CI | Partial — `examples/monorepo.yml` repeats rules/paths and artifact names, E1. | Derive selection once; no manual duplication of the same affected-path rules across lint/test/build. |
| G2 | Shared library change rebuilds dependent projects | Partial — shared paths listed manually, E1. | Transitive impact, lock/config changes, renamed/deleted files and full-run escape must be covered; missing diff base falls back safely. |
| G3 | Partial rebuild of shared Helm release | Gap — no previous approved release manifest, E6. | Preserve unchanged image digests and distinguish omitted app from deliberate deletion; do not delete workloads due to changed-only selection. |
| G4 | JS/Go/general projects beside Python | Partial — task/container commands are language-neutral; no corresponding presets, E1/E5. | Document custom command runtime/artifacts without forcing Python concepts on every project; static/docs builds need same clarity. |
| G5 | Downstream consumer compatibility and separate deploy repo | Partial — possible with native GitLab; no toolkit identity/wait contract, S1/E1. | Wait for exact consumer run and record SHA/artifact; triggering a downstream pipeline is not proof it passed. |
| G6 | Retry selected failures, cancellation, concurrency/cost caps | Partial — runner-failure retry and workflow cancellation exist, E1. | Focused reruns are diagnostic unless required release checks remain satisfied; do not promote on incomplete results; prevent accidental matrix explosion. |

### H. Schema, compiler and developer experience

| ID | Scenario | Coverage and evidence | Adverse case / acceptance condition |
|---|---|---|---|
| H1 | Small package/service configuration with discoverable defaults | Gap — project schema, presets and compiler do not exist, E1/E8. | Complete fixtures plus editor completion; no generated job names, artifact paths or duplicated endpoint configuration in the normal path. |
| H2 | Native GitLab job customization within generated pipeline | Gap — design only; MR source differs in child pipeline, S1. | Define and explain event context; reject conflicting overrides; never silently claim unchanged native semantics when context changes. |
| H3 | Custom steps producing/consuming outputs without spaghetti | Gap — typed output contract absent, E1/E4/E6. | References use public project/output names; compiler owns paths/job edges; unknown outputs/cycles rejected with source-field errors. |
| H4 | JSON Schema, Pydantic validation, deterministic explanation | Gap — only component inputs and chart schema exist. | Reject unknown toolkit keys, duplicate YAML keys, invalid combinations; safe YAML loading; versioned defaults and origin tracing; no arbitrary expression evaluation. |
| H5 | Older standalone GitLab versions and editions | Partial — lint helper exists, supported feature baseline not certified, E7/S1/S6. | Declare minimum versions and capabilities; test component/array/matrix/trigger/approval features on target instances; no silent policy downgrade. |
| H6 | Policy, secrets, SBOM/scanning/signing and notifications | Partial — protection/credentials documented, generic tasks possible; no built-in attestations/policy integration, E1/E7. | Editable project YAML cannot be its own security boundary; audit exact artifact, scoped credentials, failure reporting and offline scanner databases. |

## Executed verification

1. `python -m unittest discover -s tests -v`: **15 passed**, 0 failures. Covers local component expansion, selected guard/preview behavior, version parsing and override logic. Does not evaluate actual GitLab rules or runner behavior.
2. `python tests/integration_uv.py`: **passed** using real uv, Python 3.12.13 and a temporary local Git repository mapped from a test HTTPS URL. Candidate 1.0 replaced unavailable >=2, installed commit verified. It does not test an actual HTTPS server, private registry or all Python versions.
3. `python scripts/sync_embedded.py --check`: **passed**. Embedded copies match helper sources.
4. Helm 3.17.3 strict lint and template using `examples/helm-values.yaml`: **passed**, six objects: two ConfigMaps, two Deployments, Service, Ingress. Bad digest: **rejected**. No cluster calls.
5. Workspace adverse probe: create root `[tool.uv.workspace] members=["packages/*"]`, root/member static projects with no dependencies; `uv lock --offline` succeeds at root. Running helper from `packages/a` fails with **“Commit uv.lock; normal validation uses uv sync --locked”**. This confirms a genuine workspace boundary issue, not a missing dependency server.
6. Release-guard adverse probe: expand `deploy`, run its first guard command with no `CI_DEPENDENCY_*` variables and only `COMPONENT_DEPENDENCY_REPO` populated. Exit **0**. Combined with E2 input inspection, this shows the guard observes a particular variable convention, not prior candidate-test provenance. It does NOT show that a candidate image was actually deployed or that GitLab permissions were bypassed.
7. Inventory: **13 templates, 163 input declarations** across them (not unique or all required inputs). Examples: Python 32 lines/5 includes, uv airgap 47/7, web previews 64/6, monorepo 118/9. These are baseline measurements, not proposed-YAML measurements; values files/platform setup add more configuration.

No live GitLab CI Lint was possible with the supplied environment: no target GitLab instance credentials/configuration were supplied. No actual image build/push, package upload, deployment, rollback, OpenShift admission, Windows/macOS run or Python 3.11/3.13/3.14 matrix was performed. Existing helper unit tests and documentation are not substitutes for those checks.

## Findings that change the design

### 1. Generation mode is a user-visible choice

Runtime generation normally uses a child pipeline. There, `CI_PIPELINE_SOURCE` becomes `parent_pipeline`; MR variables remain available. Therefore a native rule matching `merge_request_event` cannot simply be copied and promised to behave identically. Current MR preview rules would fail in that context. Dynamic-child includes also have restrictions on variable use. [GitLab downstream pipelines](https://docs.gitlab.com/ci/pipelines/downstream_pipelines/)

Two defensible implementations: generate a committed CI file (preserve top-level event semantics, add drift checking), or generate at runtime (less generated-file maintenance, explicitly document child event context and parent status propagation). Compare both with actual MR fixtures before choosing. Do not silently rewrite arbitrary native expressions or spoof GitLab predefined variables. This is a blocking decision for the claim of transparent native customization.

### 2. One dependency run record must drive tests and preview builds

The current helper resolves and installs per job, restores the original lock and emits only candidate provenance. It does not preserve a resolved dependency bundle for later image builds. Add pipeline-level candidate resolution, job-specific compatible lock/environment records, and a defined build handoff. A candidate run and an ordinary release run must be distinguished by artifact provenance, not only whether a user remembered to clear variables.

### 3. A source directory is not an execution root or a release identity

uv workspaces share a root lock and root-level configuration. Project member selection, working directory, artifact output paths and package release ownership need separate meanings. API and generated SDK can share source but have separate releases. Supporting this explicitly prevents later ad hoc path exceptions. [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)

### 4. Custom charts need an image binding contract

`image_values.py` always emits `apps.<name>.image.repository/digest`. A chart using `image.repository` and `image.tag` will not automatically consume that. The extension needs an explicit, validated values binding or adapter, plus a test that the resulting workload contains the intended digest. Keep OpenShift resources and certificate provisioning in Helm rather than expanding the CI schema into Kubernetes.

### 5. Check subject and timing cannot be inferred from its name

Source tests, container tests, checks against staging and postdeployment smoke tests have different inputs/freshness. `integration` remains a user-defined name. A deployment should identify its required checks and when fresh execution is needed; the compiler must validate selection and required success. Neither a skipped job nor a tolerated failure can satisfy a mandatory gate.

### 6. Release success must be separate from deployment success

A package-only release needs no deployment. A service can have several deployments for one release. A coordinated release can partially publish and cannot generally unpublish safely. Define a release record with expected outputs, checksums/digests, versions and publication receipts; resume according to actual destination state. Do not use a global final stage as the only model of completion.

### 7. Concurrency and cleanup need state-aware behavior

`resource_group` serializes jobs but is not itself a freshness guarantee. GitLab has separate outdated-deployment controls. Preview namespace/external-secret retention must be an explicit ownership choice, and failed setup needs recoverable cleanup. Partial monorepo deployment needs prior approved image state. [GitLab deployment safety](https://docs.gitlab.com/ci/environments/deployment_safety/)

### 8. Internal-only operation needs enforcement and cold-cache proof

The runtime image configuration is a good base; a public publish default and unrestricted project package-index changes mean it is not itself an egress policy. Platform policy must select/validate endpoints and runtimes; infrastructure controls enforce isolation. Test missing wheels, private Git auth, build-backend downloads, scanner data, certificate rotation and pulls at every layer. Normal uv upgrades remain bounded by project constraints. [uv dependency synchronization](https://docs.astral.sh/uv/concepts/projects/sync/)

## YAML complexity stress test

These are design walkthroughs, not compilable fixtures. The model/compiler are absent, so no claim is made that their YAML validates. Count developer decisions and duplicated wiring, not only line count.

| Walkthrough | Developer should supply | Toolkit must derive | Assessment |
|---|---|---|---|
| Simple package | Path/preset; changed check command; release tag convention; index name if ambiguous | Dependency setup, wheel/sdist paths, required test gates, publish artifact linkage | Plausibly simple; not proven until a complete fixture compiles. |
| API with staging/production | Dockerfile/context if nonstandard, deployment destinations and check choices, Helm values | Image build identity, digest handoff, required checks, environment records | Plausibly simple; default policies must be inspectable. |
| Mixed-version/GPU tests | Native image/tags/matrix and test commands beside each check | Safe setup per job, separate results, same candidate commits, aggregate gates | Medium inherent detail; native fields avoid a parallel runner/matrix language. Windows runtime portability is actual work. |
| API exports SDK from shared path | Explicit generation command, spec output, SDK package identity and version owner | Artifact handoff, affected-project relationships, two release flows | Unresolved public output syntax; cannot honestly rate simple yet. |
| OpenShift custom chart and TLS | Chart/values, destination, explicit image-value binding and resource ownership | Same delivery/check/provenance flow as stock chart | Unresolved binding syntax; accepting chart reference alone is inadequate. |
| Partial monorepo preview + migration + production promotion | Project relationships, required migration/checks, deployment destinations | Previous digests, isolated preview state, fresh checks, promotion record and cleanup | Hardest fixture; multiple contracts unresolved. Do not squeeze these into opaque preset flags. |

Proposed measurable adoption gates:

1. A complete ordinary package example should need at most roughly 25 nonblank developer YAML lines, excluding platform setup and existing pyproject metadata. A service example should target roughly 40 CI configuration lines, with Helm values counted and displayed separately. These are targets, not measured achievements.
2. Adding a runner or Python matrix must change only that check; no copied setup, artifact paths or generated job names.
3. Adding a preview must not require a developer to write digest plumbing or checkout-dependent cleanup code.
4. A custom chart may require an explicit artifact binding but must not require replacing the deployment engine.
5. Every toolkit field must have completion, a description, a default/source and an example. Native fields must link to the appropriate GitLab docs and have clear override boundaries.
6. Effective configuration must explain check selection, resolved paths, runtime, dependency overrides, output consumers and inherited defaults before execution.
7. Unknown references, unsupported runtime combinations, ambiguous inputs, graph cycles and conflicting policy must produce source-field errors. Matrix limits and missing runtime versions are preflight failures.
8. At least one fresh reader should complete the basic package and runner-matrix tasks using only the quickstart/editor help. Until this is observed, simplicity remains a design hypothesis.

## Verified coverage boundaries and implementation order

Keep YAML plus the shared Pydantic model as the direction. Avoid adding a Python SDK merely to hide unresolved semantics. Use native execution settings, but make generated-pipeline context and owned artifact/gate wiring explicit.

First resolve the blocking contracts: generation mode; workspace/runtime scope; immutable run/output records; custom-chart image binding; release versus deployment ownership; required-check semantics. Next create complete example fixtures for the six walkthroughs, including their expected job graph and failure outcomes. Only then finalize models/schema and implement compilation.

Verification should advance in layers: schema errors and deterministic rendering; graph/identity tests; target GitLab compile and pipeline events; focused runner/uv/Helm integrations; cold-cache airgap and partial-failure release tests. Do not rerun a broad expensive matrix for documentation changes. Maintain the scenario IDs as acceptance requirements, and report whether evidence is source inspection, local execution, server compilation or live integration.

The audit does not justify calling the whole toolkit production-ready or the future YAML simple. It does justify retaining the baseline and concentrating design effort on the few interfaces that must compose correctly.

## Source register

- S1: [GitLab downstream pipelines](https://docs.gitlab.com/ci/pipelines/downstream_pipelines/) — generated children, source context and status propagation.
- S2: [GitLab job rules](https://docs.gitlab.com/ci/jobs/job_rules/) — pipeline selection and changed-file behavior.
- S3: [GitLab resource groups](https://docs.gitlab.com/ci/resource_groups/) and [deployment safety](https://docs.gitlab.com/ci/environments/deployment_safety/) — serialization versus outdated deployments.
- S4: [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/) and [syncing](https://docs.astral.sh/uv/concepts/projects/sync/) — lock ownership and upgrades.
- S5: [GitLab runner executors](https://docs.gitlab.com/runner/executors/) — image/service/shell compatibility.
- S6: [GitLab job matrices](https://docs.gitlab.com/ci/jobs/job_control/#parallelize-large-jobs) and [YAML reference](https://docs.gitlab.com/ci/yaml/) — native matrix, merging and job settings. Feature availability must be checked against the target self-managed version.
- S7: [uv package publishing](https://docs.astral.sh/uv/guides/package/#publishing-your-package) — named index and publish URL configuration.
- S8: [Audited repository snapshot](https://github.com/Heknon/generic-gitlab-cicd/tree/63388e5ff294a92953be256f7b6760c779fd88b5) — implementation evidence, not a claim of live execution.
