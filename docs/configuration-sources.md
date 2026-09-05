# Configuration sources — revision two

The CLI accepts a Git repository as an organization's maintained source of templates and configuration defaults. This feature is implemented on the review branch; it does not replace target GitLab CI Lint or live infrastructure acceptance tests.

## Administrator setup

Mirror/fork this repository to your internal GitLab. Edit `defaults/platform.yml` to use your actual runtime images, registries, namespaces and runner tags, then commit/tag that revision. Bundled internal addresses are examples, not working infrastructure. Publish matching toolkit runtime images before consumer pipelines use them.

```sh
generic-ci source add company \
  --repo ssh://git@gitlab.internal/platform/ci-toolkit.git \
  --ref v1.4.0 --default
```

The source's version tag is independent of the installed CLI version. The source manifest declares CLI compatibility. Authentication uses Git's existing SSH agent/credential helpers; interactive credential prompting is disabled. HTTPS credentials in URLs are rejected. Never commit secrets in defaults, templates or locks: defaults are included in the lock and generated pipeline.

`generic-ci source list` lists local registrations. `GENERIC_CI_HOME` optionally selects the local registry/cache directory (default: ~/.config/generic-ci).

## Developer workflow

```sh
generic-ci init                         # list templates from the default source
generic-ci init --template python-package
generic-ci validate
generic-ci explain
generic-ci render -o .gitlab-ci.yml
generic-ci render --check -o .gitlab-ci.yml
```

Available starter templates are python-package, node-service (pnpm), and image-factory. They expose editable checks and workflows; no source scripts are executed during initialization. Application starters expect existing application source, test commands and lockfiles. The image-factory starter includes example Dockerfile/configuration/version files.

For one-off onboarding without a personal registration:

```sh
generic-ci init --repo ssh://git@gitlab.internal/platform/ci-toolkit.git \
  --ref v1.4.0 --template image-factory
```

`--source company` chooses another registration. `--root` selects the consumer directory. Init refuses to overwrite existing template destinations or source/lock files. A plain init without a registered source retains the previous ecosystem starter behavior.

The project records:

```yaml
# generic-ci.yml
source:
  repository: ssh://git@gitlab.internal/platform/ci-toolkit.git
  ref: v1.4.0
delivery: delivery.yml
# Optional project platform overrides:
# platform: ci-platform.yml
```

Commit this file, generic-ci.lock.json, delivery.yml and generated CI. The lock contains the exact Git commit and source defaults. The CLI verifies lock contents against tracked Git objects. It never silently follows a branch during rendering. A developer's global default only influences initialization; existing projects always use their own source.

## Defaults and overrides

The source manifest supports `defaults.delivery` and `defaults.platform`, each pointing to a YAML mapping. Platform defaults contain the workflow platform configuration; delivery defaults contain pipeline fields. Delivery defaults merge into the pipeline as written, not into every project implicitly. Use platform `defaults` for shared execution fields.

Source mappings merge with project files; model defaults fill omitted fields. Maps merge recursively, lists/scalars replace. `--config` selects an alternative delivery file and `--platform` an alternative project platform overlay. The latter overlays source platform defaults rather than requiring another complete platform file. These explicit overrides are recorded in generated input fingerprints.

`explain --json` reports effective platform configuration, projects, generated jobs and leaf-level origins for declared source/project values. Model-provided defaults are visible in effective output but do not have a source-file origin. Defaults are overridable; this is not an organization-policy enforcement system.

## Updates and offline use

```sh
generic-ci source update --ref v1.5.0 --check  # show changes; nonzero when different
generic-ci source update --ref v1.5.0          # validate and update project lock
generic-ci render -o .gitlab-ci.yml
```

Update reports old/new commits and a defaults diff, validates the resulting complete pipeline, and preserves developer-owned template files. Review and commit the lock/config/generated YAML diff. It does not upgrade the installed CLI or images. Registrations retain their initial pin for subsequent initialization until `source add` refreshes them.

```sh
generic-ci source fetch                   # fetch the project's locked commit
generic-ci validate --offline             # require cached Git objects
generic-ci source fetch --from /media/ci-toolkit.bundle --offline
```

`--from` accepts a local Git checkout or bundle containing the locked commit. Create a portable bundle using `git bundle create ci-toolkit.bundle --all` in the source repository. For a new offline project, register that local repository/bundle with source add, then initialize from it. That records the local path; use the canonical internal Git URL for team-shared projects and import the matching objects into each machine's cache. No public fallback is attempted. Git submodules and symlink template files are not supported.

## Source manifest

```yaml
version: 1
cli: '>=0.4.0,<0.5.0'
defaults:
  platform: defaults/platform.yml
  delivery: defaults/delivery.yml  # optional
templates:
  python-package: starters/python-package
  image-factory: starters/image-factory
```

Schema files: schemas/source.schema.json and schemas/source-project.schema.json. Additional docs, schemas and skills can live in the source repository; only explicitly listed templates/defaults are consumed automatically. Templates are copied once, while defaults remain pinned and inherited. No arbitrary Python plugins, hooks or template substitution language is loaded from the source.
