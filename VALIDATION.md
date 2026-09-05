# Validation — revision one

Passed locally:

- 15 unittest checks: component input expansion, job collisions, artifact references, manual preview teardown, release guards, candidate SHA resolution, annotated tags, ambiguous refs, manifest/lock restoration, normal locked sync and version parsing/tag matching.
- Real uv integration: a local Git dependency branch replaces an unavailable >=2 requirement with a 1.0.0 candidate; installed commit and restored manifest verified.
- Embedded Python helpers compile and match their source files.
- Helm 3.17.3 strict lint and rendering of API + worker: six Kubernetes objects, matching selectors and digest-pinned images.
- Helm schema rejects malformed image digests and ingress without a service.
- Runtime tool dependency lock resolved with hashes for Python 3.12.

Not run here:

- Target GitLab CI Lint / real runner execution.
- Docker/BuildKit runtime image builds or Artifactory pushes.
- Kubernetes admission, RBAC, image pulls, live rollout/rollback or preview expiry.
- Actual PyPI/Artifactory publishing or GitLab release creation.
- Helm 4 execution (component selects its documented rollback flag).

No infrastructure deployment, package publication or remote repository mutation was performed.
