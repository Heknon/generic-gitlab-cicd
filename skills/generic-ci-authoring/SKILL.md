---
name: generic-ci-authoring
description: Create, edit, validate and render generic-ci pipelines using Git-backed organization templates and defaults. Use for this toolkit's delivery YAML, monorepo dependencies, releases, image factories and Helm deployment configuration.
---

Create reviewable authoring files and generated GitLab CI using the installed CLI. Read [the authoring procedure](references/authoring.md) for onboarding, editing, validation and troubleshooting. This package targets the 0.3.0 review interface; inspect the installed CLI/schema before using fields from memory.

Load additional references only for the task at hand:

- [CLI contracts](references/cli.md): commands, file ownership, paths and public producer references.
- [Workflow contracts](references/workflows.md): Python/Node preparation, custom checks/matrices, versions/releases, candidate dependencies, Buildah and Helm behavior/limits.
- [Configuration sources](references/sources.md): source registration, pinned defaults, overlays, upgrades and offline bundles.
- `references/examples/`: runnable configuration examples for delivery, Python packages, Bun backends, React builds and a pnpm SDK/API workspace; platform.yml supplies illustrative infrastructure.
- `references/schemas/`: bundled workflow, platform, source and source-project JSON Schemas for offline inspection. Installed CLI schema takes precedence if versions differ.

Preserve these contracts:

1. Inspect real project metadata, lockfiles, Dockerfiles and existing commands. Check names belong to the developer; there are no implicit unit/lint/integration functions. Select checks explicitly per event.
2. Keep existing source locks pinned during ordinary edits. The global default selects a source only for initialization. Never hand-edit a lock or silently upgrade the source to make validation pass.
3. `depends-on` propagates project selection; `needs` transfers same-event producer artifacts; `release.needs` orders coordinated publication. None substitutes package sources. Candidate dependencies are explicit per-run test inputs.
4. `build: true` requires exactly one output. List application/container/package explicitly for multiple outputs. Container repository/tag/digest comes from Buildah output; Helm mappings contain chart values paths.
5. Reuse organization runtime images, registry settings and runner tags. Source defaults contain no credentials, and offline use has no public bootstrap fallback.
6. Edit authoring files, then validate, explain, render and check drift. Report local compilation separately from live GitLab CI Lint and execution. Preserve the user's branch and existing authorization scope.

Do not invent unsupported keys, source plugin execution, MCP tools, arbitrary generated-job override merging, automatic FROM bindings or built-image smoke-test abstractions. Explain missing contracts when a request exceeds current support. The CLI is available to agents through their shell; this skill does not imply an MCP server is installed.
