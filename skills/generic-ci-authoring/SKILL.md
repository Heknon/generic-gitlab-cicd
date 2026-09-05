---
name: generic-ci-authoring
description: Create or modify delivery.yml workflows for the generic-gitlab-ci toolkit, including project checks, monorepo dependencies and Helm image mappings.
---

Use the installed toolkit's interface, not a remembered schema. `generic-ci schema` and `generic-ci schema --platform-schema` describe the workflow format; the older component/compiler interface is available only through `--format legacy`.

Inspect project metadata and the existing platform configuration before selecting Python, npm, pnpm or Bun preparation. Keep check names and commands owned by the team. Reuse available prepared internal images; do not insert public installation fallbacks.

`depends-on` propagates affected-project selection. `needs` connects a check/build to a same-event artifact producer, such as `sdk.build`. Neither changes package installation sources. Candidate dependencies are per-run inputs and must not be committed as permanent source substitutions.

Choose event check lists rather than inserting raw GitLab rules. For multiple build outputs, list `application`, `container`, or `package` explicitly. Helm `images[].set` maps build-output fields to values paths, not literal image addresses. Consult the current reference for partial-update prerequisites and unsupported cases.

Validate with `generic-ci validate --config <file> --platform <file>`. Inspect `generic-ci explain` and render the top-level pipeline with `generic-ci render -o <file>`. Report local validation separately from target GitLab CI Lint and live execution. Preserve the user's requested branch and publication scope.
