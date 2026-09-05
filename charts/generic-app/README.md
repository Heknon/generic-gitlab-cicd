# Generic OpenShift application chart — revision two

Chart 2.0.0 deploys each enabled `apps` entry as a Deployment and ConfigMap, with an optional Service and OpenShift Route. Images use `image.repository` plus a string `image.tag`. See [the complete API/worker values example](../../examples/helm-values.yaml).

Add this block to an app that has `service.enabled: true`:

```yaml
image:
  repository: artifactory.internal/docker/apps/api
  tag: "1.5.0"
route:
  enabled: true
  host: api.example.internal
  annotations: {}
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
    certificate: |
      -----BEGIN CERTIFICATE-----
      REPLACE_WITH_CERTIFICATE_PEM
      -----END CERTIFICATE-----
    key: |
      -----BEGIN PRIVATE KEY-----
      REPLACE_WITH_PRIVATE_KEY_PEM
      -----END PRIVATE KEY-----
    caCertificate: |
      -----BEGIN CERTIFICATE-----
      REPLACE_WITH_CA_CHAIN_PEM
      -----END CERTIFICATE-----
```

`key` is the OpenShift field for the private key. These are illustrative placeholders, not usable certificates. The chart passes TLS fields through without inspecting certificate contents or enforcing organizational certificate policy.

- `edge`: the router terminates TLS and forwards HTTP to the service. Omit custom PEM fields to use the router's configured certificate.
- `reencrypt`: the router terminates client TLS and establishes TLS to the backend. `destinationCACertificate` supplies the backend trust chain; it is separate from `caCertificate`, which completes the client-facing certificate chain. Configure the application to serve HTTPS on its service target port.
- `passthrough`: TLS terminates in the application. Set `termination: passthrough`; the application owns its certificate and key.
- Omit `tls` for an unsecured Route. Omit `route` or set `route.enabled: false` for no Route.

Optional Route fields include `host`, `path`, `annotations` and `wildcardPolicy`. Omitting the host lets OpenShift assign it. Routing goes to the app's generated Service and named `http` port. The name `http` is only a port identifier; re-encryption/passthrough backends can serve TLS there. The Route API determines supported field combinations.

TLS behavior follows [OpenShift's Route documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/ingress_and_load_balancing/routes).

## Using certificate files

Helm supports loading PEM contents directly from files:

```sh
helm upgrade --install backend ./charts/generic-app \
  --namespace my-team \
  -f deploy/values/common.yaml \
  -f deploy/values/production.yaml \
  --set-string apps.api.image.tag=1.5.0 \
  --set-file apps.api.route.tls.certificate=/run/certs/tls.crt \
  --set-file apps.api.route.tls.key=/run/certs/tls.key \
  --set-file apps.api.route.tls.caCertificate=/run/certs/ca.crt
```

For re-encryption, also set `apps.api.route.tls.termination=reencrypt` and optionally load `apps.api.route.tls.destinationCACertificate` from the backend CA file. You can also supply TLS through an additional values file. Keep actual private keys in your chosen secret-delivery mechanism rather than committed examples. Inline Route keys are present in Helm release data and rendered manifests; account for that when configuring access and CI artifacts. This chart does not issue or rotate certificates.

The toolkit's workflow deployment uses ordered `values` files and image bindings. The `--set-file` example above is a direct Helm command, not a new workflow YAML field.

## CI image binding

```yaml
images:
  - from: api.build-image
    set:
      repository: apps.api.image.repository
      tag: apps.api.image.tag
```

Buildah still reports the pushed digest for build evidence and consumers of other charts; this chart renders the tag. Publishing the chart to a shared Helm repository is separate from committing it here. Once published, use chart name `generic-app`, version `2.0.0` and your repository URL.

## Migration from chart 1.0.0

Replace `image.digest` with `image.tag`, `ingress` with `route`, and CI digest bindings with tag bindings. A former `ingress.tlsSecret` is not a Route TLS field; supply PEM values or use the router's configured certificate. Update the pinned chart version. Existing digest-only component adapters must continue using chart 1.x or be updated before adopting 2.x. Unknown legacy fields are not migration aliases.

Deployment defaults, probes, resource settings, existing secret references, service accounts, node selection and tolerations are otherwise unchanged. The chart does not provide persistent volumes, Jobs/CronJobs, HPA, sidecars or certificate management.
