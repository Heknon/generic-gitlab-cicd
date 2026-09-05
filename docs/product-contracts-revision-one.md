# Product contracts — revision one

This feature branch implements a project YAML compiler on top of the original component baseline. `delivery.yml` and `ci-platform.yml` are authoritative. Generated `.gitlab-ci.yml` is committed, not dynamically executed as a child pipeline: native GitLab rules retain top-level event semantics. The plan job hashes the authoring files and rejects drift. Runtime and compiler versions must match. Compilation does not execute project code or contact the network.

## Ownership and customization

Projects own directories, commands, public step/output names, checks, containers, packages, deployments and releases. Platform configuration supplies approved runtimes, hosts, registry prefixes, target namespaces, chart defaults and mandatory checks. YAML settings are not a security boundary against an author who can change the platform or generated jobs: GitLab permissions, protected environments and runner credentials remain the enforcement boundary.

Presets supply lint/unit checks and package or container defaults. A supplied check field replaces the corresponding preset field; lists replace lists. `false` disables an optional check. Empty deployment/build `checks` means no optional checks; omitted means all enabled project checks. Mandatory platform checks cannot be removed. Native `gitlab` fields are explicitly supported execution settings, not arbitrary overrides of compiler-owned script/needs/artifact wiring. Matrices use native cartesian semantics but render explicit jobs so each result has a separate receipt and artifact directory.

## Public references and artifacts

Check/step `needs` references use `step` or `project.step`; built outputs are `project.package` and `project.container`. Dependency cycles and unknown references fail compilation. Steps declare named output paths; consumers receive checksum-verified copies at the same repository-relative paths. Every required matrix variant must pass. Output collisions fail, never silently overwrite conflicting files.

Jobs write success receipts only after completion, recording configuration hash, commit and pipeline identity. Downstream jobs reject absent, stale or unsuccessful receipts and changed/missing artifact files. GitLab `needs` entries are optional only to allow rule-excluded jobs to compile: runtime success gates still fail closed. Disabling a producer through a native rule never counts as successful validation. A required job cannot use `allow-failure: true`.

## Source selection and execution

Changed-only selection uses a valid Git diff base, project paths, watch globs and transitive project relationships. Missing history or manual/scheduled/tag runs use the full project set. Generated jobs remain visible; unaffected jobs exit before expensive work and do not emit success receipts. Required upstream projects are also selected, so an affected shared deployment builds its complete image set. This deliberately rebuilds unchanged members of a shared release; importing previous release state is not implemented.

## Dependency preparation

Runtime inputs are exclusive: single `CI_DEPENDENCY_REPO/REF/PACKAGE` fields, JSON `CI_DEPENDENCY_OVERRIDES`, or `CI_DEPENDENCY_FILE`. Overrides use repository/ref/package/subdirectory and optional projects scope. Legacy `repo` is accepted. Branches/tags resolve once in the plan job; credentials never belong in URLs. Each applicable job verifies the installed candidate repository and commit.

uv workspace root owns the temporary manifest/lock. Member selection is explicit during synchronization; interpreter downloads are disabled. Policies select groups, extras, interpreter and upgrades. Candidate/upgrade changes remain active while commands run and are restored even on failure. Receipts preserve the resolved lock and identity; virtual environments are never shared as artifacts. Matrix `PYTHON_VERSION` also verifies the installed interpreter.

Container candidate support is opt-in with `dependency-bundle: true`: the runtime builds hash-locked wheels from the prepared environment and passes a named BuildKit context. The Dockerfile must consume it. The build checks canonical dependency lock identity against required source checks; changed index state causes failure rather than publishing an untested resolution. A Dockerfile is arbitrary code: consumption of a named context alone cannot prove the application's actual installed environment. Treat the supplied recipe plus container tests as the supported contract.

## Deployment and release

Deployments require selected check receipts and pushed image records. Custom charts must provide explicit repository/digest values paths. Rendering must include each required digest reference before rollout. Fresh `before` commands run inside the deployment job after manual approval; `after` commands run after rollout and fail the job on failure. Migrations are user commands, and no database rollback is implied by Helm's resource rollback. Post-rollout smoke failure does not automatically roll back a completed upgrade.

Preview jobs use a separate registry prefix and auth file, same-project MR rules, a matching stop job/resource group and no-checkout/no-artifact teardown. Stop removes chart-owned resources only; namespace/external secrets are platform-owned. Credentials must be available to the stop environment independently of checkout. Required previews block on failure; manual previews therefore remain blocking until run unless the project excludes them from its chosen workflow.

Publication requires a protected tag, exact static project version and an ordinary dependency plan. Packages publish the built artifacts to a named `tool.uv.index` with an allowed `publish-url`. No public fallback exists in the new product. Release dependencies wait for exact same-pipeline receipts. Same-pipeline coordinated releases must use a common tag convention; independently tagged cross-repository orchestration is not implemented. A partial publication emits a receipt before GitLab release creation, but resuming duplicate package uploads must be reconciled at the registry; do not promise atomicity or automatic idempotent recovery.

## Infrastructure and review gates

Every runtime must have the matching installed toolkit and its action tools: Python/uv/Git, build/twine/pip for packaging, BuildKit for image jobs, Helm for deployment, glab for release creation. Shell runtime uses installed tools rather than image selection. No public bootstrap is inserted. Host validation supplements internal configuration and network isolation; it is not a sandbox for project commands.

The original components remain available and self-contained. The product compiler is a separate authoring entry point, not an incompatible replacement. Target GitLab compilation and actual runner/registry/cluster integrations are release gates. The audit's stateful/GitOps/Terraform/progressive delivery cases use custom steps/charts or external tooling; those external engines are not reimplemented here.
