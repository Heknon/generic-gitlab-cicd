# Generic OpenShift application chart — revision three

Chart 2.1.0 deploys each enabled `apps` entry as a Deployment and ConfigMap, with an optional Service and OpenShift Route. Images use `image.repository` plus a string `image.tag`. See [the complete API/worker values example](../../examples/helm-values.yaml).

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

Optional Route fields include `host`, `path`, `annotations` and `wildcardPolicy`. Omitting the host lets OpenShift assign it. Routing goes to the app’s generated Service. `route.targetPort` selects its named port and defaults to `http`. The name `http` is only a port identifier; re-encryption/passthrough backends can serve TLS there. The Route API determines supported field combinations.

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

Buildah still reports the pushed digest for build evidence and consumers of other charts; this chart renders the tag. Publishing the chart to a shared Helm repository is separate from committing it here. Once published, use chart name `generic-app`, version `2.1.0` and your repository URL.

## Migration from chart 1.0.0

Replace `image.digest` with `image.tag`, `ingress` with `route`, and CI digest bindings with tag bindings. A former `ingress.tlsSecret` is not a Route TLS field; supply PEM values or use the router's configured certificate. Update the pinned chart version. Existing digest-only component adapters must continue using chart 1.x or be updated before adopting 2.x. Unknown legacy fields are not migration aliases.

Existing secret references, service accounts, node selection and tolerations remain supported. Chart 2.1.0 changes security defaults and makes probes/resources optional, as described below. The chart does not provide persistent volumes, Jobs/CronJobs, HPA, sidecars or certificate management.


## Volumes, ports and monitoring

Each app accepts native Kubernetes `volumes`, `volumeMounts`, `ports`, `podSecurityContext`, `securityContext`, `podLabels`, and `podAnnotations`. `service.ports` is a native Service port list. Lists supplied in a later values file replace earlier lists; they are not appended by name.

For example, add these settings to an app:

```yaml
volumes:
  - name: settings
    configMap:
      name: api-settings
  - name: data
    persistentVolumeClaim:
      claimName: api-data
volumeMounts:
  - name: settings
    mountPath: /app/config
    readOnly: true
  - name: data
    mountPath: /data

ports:
  - name: http
    containerPort: 8080
  - name: metrics
    containerPort: 9090
service:
  enabled: true
  ports:
    - name: http
      port: 80
      targetPort: http
    - name: metrics
      port: 9090
      targetPort: metrics
route:
  enabled: true
  targetPort: http
  host: api.example.internal

podLabels:
  team: backend
podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "9090"
  prometheus.io/path: /metrics
```

This example references an existing ConfigMap and PVC; the chart does not provision those resources. Other native volume types, including Secret, projected and emptyDir volumes, also pass through. No volume or mount is injected automatically.

Container ports describe where the application listens; they do not start a listener or change its configuration. Service ports expose those listeners inside the cluster. The Route selects one Service port for external access. Declaring a metrics port does not implement a metrics endpoint: the app must already serve it.

Monitoring annotations are metadata. They are effective only when the Prometheus discovery configuration honors them. Operator installations may instead select Services or Pods through ServiceMonitor/PodMonitor resources; those resources remain externally managed. `service.labels` and `service.annotations` are also available. Chart identity labels and the configuration checksum remain on Pods for Service selection and configuration-triggered rollouts.

Existing `service.port` and `service.targetPort` single-port values remain supported. If omitted, the shorthand uses Service port 80 and container port 8080, named `http`. Explicit `ports` and `service.ports` replace the respective shorthand lists. App `ports` can also be used with `service.enabled: false`.

## Security, probes and resources

By default, the chart omits pod/container security contexts and `automountServiceAccountToken`. It does not force non-root execution, a read-only root filesystem, dropped capabilities or disabled token mounting. Image, service-account and cluster admission defaults apply, including OpenShift SCCs. The chart does not request privileged execution or bypass cluster restrictions.

Configure only what your application needs:

```yaml
podSecurityContext:
  runAsNonRoot: true
securityContext:
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
automountServiceAccountToken: false

volumes:
  - name: tmp
    emptyDir: {}
volumeMounts:
  - name: tmp
    mountPath: /tmp
```

All probes and `resources` can be omitted or set to `{}`. Resource requests and limits can be specified independently. The chart does not impose health-check or resource-allocation policy.

When upgrading from 2.0.0, explicitly add the previous security settings and `/tmp` emptyDir if you want to retain that behavior. The previous temporary volume had `sizeLimit: 256Mi`; the previous pod context used `runAsNonRoot: true` and `seccompProfile: {type: RuntimeDefault}`; the previous container context used `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, and `capabilities: {drop: [ALL]}`; token mounting was disabled. Init containers, sidecars and other additional workload types are not introduced in this revision.
