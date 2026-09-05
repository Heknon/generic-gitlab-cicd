# 0.3.4

- Showcase monorepo testing, package publication, container builds and OpenShift deployment in one README example.
- Simplify onboarding around guided setup and editor schema generation.

# 0.3.3

- Add interactive and unattended setup for organization templates and standalone services.
- Generate local editor schemas, optional OpenShift preview values, and setup notes without overwriting existing files.
- Rewrite onboarding documentation around setup and explain editor schema mappings.

# 0.3.2

- Reject generated job-name collisions and empty check matrices; omit empty runner tags.
- Close deployment selection over contextual artifacts and overlapping deployments; reject incompatible complete-release tag conventions.
- Recover shallow Git history for forward deployment checks while continuing to reject rollback/nonlinear updates.
- Preserve script shell state and Git dependency subdirectories in image wheelhouses.
- Add regression tests, pinned gitlab-ci-local execution scenarios and deliberate disposable GitLab/Runner E2E tooling.
- Require Helm and local pipeline validation in the PyPI release workflow.

# Changelog

## 1.0.0 — revision one

Initial CI component framework with typed configuration, uv candidate dependency validation, version gates, packaging, registry publishing, rootless image building, air-gapped runtime image preparation, protected releases, deployment adapters, preview cleanup and a multi-application Helm chart.
