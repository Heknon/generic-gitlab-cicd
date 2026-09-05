# CI framework maintenance

- Preserve consumer stage barriers. Do not add needs: [] to build/publish/deploy jobs without explicitly preserving validation gates.
- Components must be self-contained: files from this repository are not automatically checked out in a consumer pipeline.
- Edit Python helpers under scripts/, then run python scripts/sync_embedded.py. Check with --check.
- Keep default runtime acquisition internal. Never add a public bootstrap curl, apt, container image or package index to the air-gapped path.
- Never bake credentials into images or configuration. Maintain independent runner, BuildKit, runtime and Kubernetes trust configuration.
- Candidate dependency validation must record and verify immutable commits, restore manifests/locks on failure and stay separate from release publication.
- Preview stop jobs must remain manual, share start selection rules/resource groups, and work without checkout/artifacts.
- Run the affected unittest class first during fixes, then the full inexpensive local suite before release. Run the uv integration test when dependency resolution changes; Helm lint/render when chart or Helm components change.
- Do not retry failing tests until they pass or bypass failed gates. Automatic retries are for runner failures only. Do not rerun image factories for unrelated documentation fixes.
- Local YAML expansion is not GitLab CI Lint. Validate compile behavior on the target GitLab before publishing a component release.
