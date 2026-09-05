# Implementation checkpoint — revision one

Branch: `feature/coherent-ci-product`. This is an unfinished implementation for review, not a production release.

The branch contains a Pydantic/YAML compiler, committed-pipeline generation, uv workspace/override preparation, artifact/check receipts, initial deployment/publication adapters, example configurations and local regression tests. The original component workspace helper is also repaired. Live GitLab compilation, runtime image builds, registry publication, cross-platform execution and cluster deployment have not been performed.

During implementation the product owner challenged whether existing delivery products should own these responsibilities. Further custom deployment-engine development is paused for that architectural decision. Preserve the work as a comparison/prototype; do not interpret this checkpoint as approval to ship the custom runtime.

## Established alternatives examined

- [GitLab Auto DevOps](https://docs.gitlab.com/topics/autodevops/) supplies default CI and Kubernetes review/deployment workflows; [customization](https://docs.gitlab.com/topics/autodevops/customize/) supports custom charts. Assess the actual uv, monorepo and disconnected setup rather than assuming defaults fit.
- [Argo CD ApplicationSet GitLab MR generator](https://argo-cd.readthedocs.io/en/latest/operator-manual/applicationset/Generators-Pull-Request/#gitlab) supports self-hosted GitLab and preview selection. [Sync hooks/waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/) provide deployment sequencing. CI must still signal that the exact candidate image has passed required tests; MR discovery alone is not that signal.
- [werf GitLab integration](https://werf.io/getting_started/cicd/gitlabcicd-hostrunner-linux-docker-bestpractice-no-monorepo.html) includes review environments and cleanup. [Bundles](https://werf.io/docs/v2/usage/distribute/bundles.html) package charts with image references; [deployment scenarios](https://werf.io/docs/v2/usage/deploy/deployment_scenarios.html) cover distribution across disconnected boundaries. This is a concrete alternative to custom image/chart/lifecycle machinery, not a complete Python-package CI system.
- [Devtron](https://docs.devtron.ai/docs/user-guide/creating-application/workflow/cd-pipeline) offers an integrated delivery product with [air-gapped installation documentation](https://docs.devtron.ai/docs/setup/install/install-devtron-in-airgapped-environment). Verify current edition/licensing, offline operation and the exact MR-preview workflow before selection; an ephemeral debugging container is not a preview environment.

The provisional recommendation is GitLab CI + Argo CD/ApplicationSet for a persistent Kubernetes delivery platform, or GitLab CI + werf when deployment should stay driven by CI jobs. Avoid building both into the toolkit before choosing ownership. Keep custom uv candidate preparation, internal runtime packaging and small reusable CI defaults where they add specific value.

Outstanding prototype limitations include complete publication recovery, preview cleanup reconciliation, prior-release reuse for partial builds, deployment freshness enforcement, end-to-end candidate container validation, runtime factory verification, and complete executable application examples. Existing illustrative examples need real internal hosts, approved images, lockfiles and application-specific checks. These must not be represented as completed scenario integrations.
