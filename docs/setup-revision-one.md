# Setup — revision one

Run `generic-ci setup` from an existing application repository, using the installed CLI. Setup stages its proposed configuration, validates and renders it with the normal CLI, lists the files and delivery settings, then asks before writing. Existing files and symlink destinations are rejected. Application commands are never executed. There is no pre-commit integration.

## Organization configuration

Choose organization mode and supply a configuration source Git repository and revision, or a named registered source. GitHub and GitLab URLs use the same existing Git source loader; credentials belong in your Git configuration. The source must have a generic-ci source manifest, not merely be an application repository. Setup lists available templates if no template was supplied.

```sh
generic-ci setup --yes --mode organization \
  --repo ssh://git@gitlab.internal/platform/ci-config.git \
  --ref v1.0.0 --template python-service
```

The source loader pins the exact commit. Setup lets you select an existing template application, confirm its directory, and confirm its command when it has a single check with a single script entry. Multi-check templates retain their declared commands. Infrastructure and deployment settings come from the template/defaults; edit the resulting configuration for further customization. `--app` selects an existing template project; it does not rename it or rewrite cross-project references.

Use `--source company` for a registered source instead of `--repo`/`--ref`. `--offline` requires cached source objects. A dry run may populate the source cache, but does not write application files. Source templates remain responsible for their application and chart files; setup does not invent a template variable/interpolation language.

## Standalone service

The standalone path detects Python/package.json and local pnpm/Bun locks. You confirm or override that detection. It suggests a Node test command only when a test script exists; otherwise provide your actual command. It asks for prepared runtime and builder images, registries and a runner tag. These images must already contain the matching toolkit and required tools.

```sh
generic-ci setup --yes --mode standalone --app api --path . \
  --ecosystem python --test-command 'uv run --no-sync pytest' \
  --runtime-image registry.internal/ci/python-toolkit:0.3.2 \
  --builder-image registry.internal/ci/buildah-toolkit:0.3.2 \
  --registry registry.internal/apps \
  --preview-registry registry.internal/previews --runner-tag internal-linux \
  --dry-run
```

Remove `--dry-run` to write. `--yes` means no prompts: missing required answers fail. Use `--root` to select an existing repository. The first standalone version sets push/MR checks; customize other events, builds or multiple applications in delivery.yml afterward.

For an optional deployment, add:

```sh
--deploy yes --helm-image registry.internal/ci/helm-toolkit:0.3.2 \
--chart-oci oci://registry.internal/charts/generic-app --chart-version 2.1.0 \
--namespace previews --hostname auto --port 8080 --tls edge
```

These are additional flags for the preceding command. Deployment requires an existing application Dockerfile and a published generic-app 2.x compatible chart. Setup generates a container build, a manual MR preview deployment, and `deploy/values.yaml`. `auto` omits the Route hostname so OpenShift assigns one; literal hostnames must be unique across previews. CI variables inside Helm values are not expanded. Supported TLS choices are edge, reencrypt, passthrough and none; supply the appropriate certificates/backend trust and ensure your application protocol matches the choice. Setup generates no private keys or production release workflow.

Provide `PREVIEW_KUBECONFIG` as a GitLab file variable, registry authentication, internal CA trust, image pull secrets and namespace resources through your platform. Edit probes, resources, secrets and mounts for your application. `CI-SETUP.md` lists remaining work. Run GitLab CI Lint and a real pipeline before adoption.

## Editor completion and validation

Setup exports schemas from the installed CLI:

| Local schema | Associate with |
| --- | --- |
| `.generic-ci/delivery.schema.json` | `delivery.yml` (or the source descriptor's delivery path) |
| `.generic-ci/platform.schema.json` | `ci-platform.yml` or the configured platform overlay, if present |

It adds a relative `# yaml-language-server: $schema=...` comment to those authoring files for editors using YAML language server. Commit schemas with the generated files for offline editing. A schema describes accepted fields and types; `generic-ci validate` additionally validates references and the job graph.

In PyCharm, open **Settings → Languages & Frameworks → Schemas and DTDs → JSON Schema Mappings**, add each local schema and map its YAML file. Alternatively use the YAML editor's schema selector and choose **New Schema Mapping**. See [JetBrains' YAML schema instructions](https://www.jetbrains.com/help/pycharm/yaml.html).

Export or refresh schemas yourself after upgrading the toolkit:

```sh
generic-ci schema -o .generic-ci/delivery.schema.json
generic-ci schema --platform-schema -o .generic-ci/platform.schema.json
```

Do not map these to `.gitlab-ci.yml` (GitLab's schema) or `deploy/values.yaml` (your chart's `values.schema.json`). Setup does not fetch a remote chart's schema. For the bundled chart, use `charts/generic-app/values.schema.json` from the matching chart revision.
