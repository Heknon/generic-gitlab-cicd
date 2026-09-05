# Upgrade to 0.4

0.4 repairs delivery ordering and makes release side effects explicit. Review this before regenerating an existing consumer pipeline.

| Area | Required migration or new behavior |
|---|---|
| Runtime | Provide Python 3.11+, Git, Bash and generic-ci 0.4.x in every selected role image. Buildah/Helm/Node roles retain their own tools. 0.3 images cannot execute protocol 2. Compatible 0.4 patch releases share the protocol; future incompatible behavior must change it. |
| GitLab Release entry | Add `gitlab: true` under project `release` if you want a GitLab Release entry. Default false needs no release API token. Tag creation through `release.create` still needs a protected API token. |
| Package publication | Keep explicit workflow `publish`. A release workflow alone does not upload a package. Its completion job is now `release:PROJECT:release`; publication remains `release:PROJECT:publish` when selected. |
| Release ordering | `release.needs` gates completion after prerequisites' publication. Approval and deployment wait for completion. To consume a newly published package during a build, explicitly order that build with `needs: [sdk.publish]` and configure the manifest/lock accordingly; no dependency is silently rewritten. |
| Version policy | `require-bump` governs MR advancement. Routine push/schedule/manual/API checks may test an already released version. Creating a new release still requires version advancement. |
| Source compatibility | Organizations review their source manifest and update `cli` to include `>=0.4.0,<0.5.0`. Publish a reviewed source revision; consumer upgrade can stage it using `--source-ref REF`. Do not alter a source lock by hand. |
| Tests-only platforms | `container-builder` and `registries` can be omitted until container builds are selected. Node/Bun images may run the planner. |
| Selection | Chart/value changes select their image owners; native rules omit unrelated jobs. Known unavailable diff bases fail with an actionable fetch message. `watch` supports portable *, **, ?, and bracket globs; brace expressions are rejected. |
| Shell | Commands execute together under Bash `-euo pipefail`. A failed command in a pipe now fails the job. |
| Explain | Default output is readable text. Use `--json` for structured results, effective configuration and source origins. |

Install the reviewed CLI from your approved source. Preview changes with `generic-ci upgrade`, add explicit role bindings with repeated `--runtime-image ROLE=IMAGE`, and apply with `--apply`. The command refreshes local schemas and generated CI together while preserving application/template files. It does not build images, install the CLI, or discover credentials.

Run `generic-ci doctor`, inspect `explain --event ...`, and use the target GitLab CI Lint before adoption. Retain existing 0.3 runtime images for pipelines already generated with that version until their deployments and stop jobs are no longer needed.
