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
