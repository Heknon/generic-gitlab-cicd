# Kubernetes delivery with Helm

Use Helm for application delivery; use Terraform separately for clusters, node pools, network, DNS and other infrastructure. Helm provides a release boundary around a set of Kubernetes manifests and rollout/rollback commands. This chart has no dependency downloads.

## Chart contract

`apps` is a map keyed by a short, stable service name. Each enabled application receives a Deployment and ConfigMap, plus optional Service and Ingress. API servers and workers are demonstrated in `examples/helm-values.yaml`.

Configure each application's immutable image repository/digest, replica count, requests/limits, readiness probe, optional startup/liveness probes, commands/arguments, environment, ConfigMap entries and existing secret references. Enable ingress only with a service; supply ingress class, host and TLS secret. Use distinct hosts across releases.

The example image digest is deliberately a placeholder. Supply a real pushed digest; schema validation can validate format, not registry existence. The example API assumes health endpoints on port 8080. The worker assumes `/app/healthcheck` exists. Change these to match your application.

Pods require non-root-capable images, drop Linux capabilities, disable privilege escalation and service-account-token automount, and use a read-only root filesystem with a writable /tmp. OpenShift can assign a UID; no fixed runAsUser conflicts with that allocation. Applications needing other writable paths need chart extensions. Resources and readiness are required because an otherwise generic chart can appear deployed while the application is unavailable.

Pre-create existing application secrets, TLS secrets, image pull secrets and optional service accounts in the namespace. This chart does not store secret values. ConfigMap changes roll pods through a checksum annotation; external Secret changes require an explicit rollout or your existing secret-reloader controller.

This chart targets stateless Deployments. It does not provision databases, persistent storage, CRDs, cluster RBAC, autoscaling, disruption budgets or network policies. Add those according to the actual application and platform policy; generic allow-all policies or guessed database migration hooks would not be robust.

## Production

1. Publish the chart internally:
   `helm package charts/generic-app`, then `helm push generic-app-1.0.0.tgz oci://artifactory.internal/helm-local`.
   Authenticate with a scoped runtime Helm registry config. Do this as a versioned platform release; application jobs pin chart-version.
2. Build each image via `container-build`, push using a commit-specific reference, and preserve separate metadata artifacts.
3. Include `helm-deploy`, set namespace/release/chart/version/values and list image build jobs under dependencies.
4. Pass the image mappings as JSON. Every metadata file must be from the current pipeline:

```json
[
  {"app":"api","repository":"artifactory.internal/docker-local/api","metadata":"api-build-metadata.json"},
  {"app":"worker","repository":"artifactory.internal/docker-local/worker","metadata":"worker-build-metadata.json"}
]
```

The helper writes a Helm overlay with the real `containerimage.digest` values. Use the exact same repository as the builder's destination without its tag. Baseline values supply the other workload settings. Helm merges application maps, so all apps in your baseline remain part of that release even if only one image changes. For changed-only builds, pin unchanged services' previously approved digests in baseline values; do not remove their app entries accidentally.

The component waits for readiness and uses Helm 3 `--atomic` or Helm 4 `--rollback-on-failure`, selected from the installed CLI major. Namespace plus release name identifies the resource lock. Different clusters with the same pair may serialize unnecessarily, but cannot race through the same lock. Enable GitLab's prevention of outdated deployments; serialization alone does not guarantee chronological deployment order.

A failed Helm upgrade can roll back manifests, but cannot undo an external database migration or a consumed message. Use backwards-compatible migration and service rollout strategies. Multiple services in one release are coordinated, not an instantaneous all-or-nothing application switch.

## Preview environments

`helm-preview` uses the same image mapping contract, defaults to `review-$CI_PROJECT_ID-$CI_MERGE_REQUEST_IID` and starts manually on same-project MR pipelines. Enable automatic previews via deploy-when only when the source and preview credentials are trusted. Fork MRs are excluded by default.

Supply a dedicated `deploy/preview.yaml`: reduced replicas, preview secrets, non-production configuration and unique MR-specific ingress hosts. Helm does not expand shell variables inside YAML. Generate the preview values file in a preparation job or `setup` command (with Python/yq), and pass that exact file. Set the GitLab environment URL to the same host. This avoids silently pointing every MR at the production hostname.

`examples/helm-preview.yml` includes `container-preview-build` to build and push MR images into a dedicated preview repository, then deploy their exact digests. Set PREVIEW_IMAGES_REPOSITORY, PREVIEW_REGISTRY_AUTH_FILE and PREVIEW_DOMAIN. Preview registry credentials must only allow the preview repository and must be available to the selected same-project MR jobs. The preview builder rejects destinations outside that prefix; production container-build still requires protected refs.

The example generates unique ingress hosts from a dedicated preview values file. Its Helm image additionally needs PyYAML. Use preview-specific TLS/application secrets and avoid production values. The application Dockerfile decides what goes into the image; test-only uv dependency overrides do not automatically rewrite Docker builds.

Start and stop jobs share selection rules, stage and resource lock. The stop job uses GIT_STRATEGY=none, downloads no build artifacts and only requires Helm plus cluster authentication. It uninstalls the release, including its chart-managed resources, after manual stop or expiry. It intentionally retains the empty namespace and externally managed secrets. Namespace deletion belongs to a restricted janitor that can verify ownership, age and absence of other workloads.

For completely custom platforms use `preview` with your own deploy/teardown commands. Its stop commands must work entirely from the runtime image because the branch may already have been deleted.

## Local and cluster validation

```sh
helm lint charts/generic-app -f examples/helm-values.yaml --strict
helm template smoke charts/generic-app -f examples/helm-values.yaml > rendered.yaml
```

After supplying your real configuration, use a staging cluster to validate RBAC, admission policies, registry CA trust, image UID compatibility, readiness and ingress. A successful template render does not establish these environment-specific properties.
