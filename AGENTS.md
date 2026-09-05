# CI framework maintenance

- Preserve consumer stage barriers. Do not add needs: [] to build/publish/deploy jobs without explicitly preserving validation gates.
- Components must be self-contained: files from this repository are not automatically checked out in a consumer pipeline.
- Edit Python helpers under scripts/, then run python scripts/sync_embedded.py. Check with --check.
- Keep default runtime acquisition internal. Never add a public bootstrap curl, apt, container image or package index to the air-gapped path.
- Never bake credentials into images or configuration. Maintain independent runner, builder, runtime and Kubernetes trust configuration.
- Candidate dependency validation must record and verify immutable commits, restore manifests/locks on failure and stay separate from release publication.
- Preview stop jobs must remain manual, share start selection rules/resource groups, and work without checkout/artifacts.
- Run the affected unittest class first during fixes, then the full inexpensive local suite before release. Run the uv integration test when dependency resolution changes; Helm lint/render when chart or Helm components change.
- Do not retry failing tests until they pass or bypass failed gates. Automatic retries are for runner failures only. Do not rerun image factories for unrelated documentation fixes.
- Local YAML expansion is not GitLab CI Lint. Validate compile behavior on the target GitLab before publishing a component release.

## Workflow authoring revision

- `generic_ci/workflows/` implements the team-defined checks/event interface. Legacy compiler remains behind `--format legacy`; do not confuse design examples with executable examples.
- Buildah is the default builder and image factory engine. Preserve its repository/tag/registry-digest output contract.
- `depends-on` propagates affected-project selection. `needs` transfers explicit same-event producer artifacts. Release `needs` orders coordinated publication. Never treat these as implicit package source substitution.
- Test the affected workflow class first. Run `tests/integration_uv.py` after Python preparation changes and the offline Node install fixture after Node preparation changes. Never run the image factory for documentation-only changes.
- Keep schema files generated from their corresponding models. User-selected checks and event workflows must not introduce hidden test suites or require native GitLab rules.
- Do not claim live GitLab, Buildah/OpenShift, Helm rollout, registry publication, Bun or pnpm validation based solely on mocked commands or YAML parsing.

## Configuration sources

- Source repositories supply data and starter files only; never execute source plugins/hooks.
- Rendering must use the committed source lock, never a moving branch or the user’s global default.
- Preserve template ownership: source updates change inherited defaults and locks, never consumer files.
- Test source changes with `python -m unittest discover -s tests -p test_sources.py`; include real Git/bundle tests for cache and pinning behavior.

## AI documentation

- Read docs/ai-authoring-revision-three.md for consumer authoring; docs/cli-reference.md defines CLI/path contracts.
- The portable authoring skill lives under skills/generic-ci-authoring. Keep it self-contained for installations outside this repository.
- Update canonical docs/examples/schemas, then run python scripts/sync_authoring_skill.py. CI checks the bundle with --check.
- Distinguish implemented workflow/source interfaces from historical design documents and the unimplemented MCP server.

## Package publication

- Development snapshots use a temporary manifest copy. Preview destinations are optional overrides; absent an override, use the normal package destination. Recommend separation without enforcing organizational publication policy.
- Preserve checks and explicit artifact prerequisites when inferring package builds from publication.
- Distinct configured URLs cannot prove Artifactory virtual repository isolation. Document repository membership and scoped credentials as recommendations.
- Preserve archive metadata validation and exact-version receipts. Development publication must not police fork status, candidate inputs, or shared repository choices. Leave registry permissions and overwrite policy to the configured platform; do not delete existing versions to fix a retry.

## Shared chart

- Chart 2.x uses OpenShift Routes and repository/tag images. TLS settings map directly to Route fields, including certificate, key, caCertificate and destinationCACertificate.
- Keep chart values examples, schema and workflow image bindings aligned. Buildah still emits digest evidence; legacy digest-only adapters target chart 1.x.
- Run chart render tests with Helm available, plus strict lint. Certificate examples contain placeholders only; do not claim a live OpenShift rollout from local renders.

## Test layers and cost

- Follow docs/testing-revision-one.md. Use affected unittest classes and `python tools/testing/local.py --scenario NAME` while fixing; run the full inexpensive suite before finalizing.
- `npm ci --ignore-scripts` installs the pinned maintainer-only gitlab-ci-local tool. Local pipeline tests use isolated workspaces and artifacts; never disable schema validation or receipt assertions to get a pass.
- Real GitLab E2E is deliberate: workflow dispatch or the same-repository PR label `run-gitlab-e2e`. Remove the label after qualification to avoid expensive runs on later commits. No nightly or broad matrix by default.
- E2E requires Docker; missing Docker means not run, never passed. The native manual-gate fixture is not evidence of a Helm rollout. Preserve exact scenario/commit/version evidence and keep failed expectations independent from implementation.
- Helm must be present in release validation; do not count skipped chart tests as a successful release gate.
