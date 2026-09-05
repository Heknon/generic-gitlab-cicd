# Testing — revision one

This repository has three distinct test layers. Passing local execution does not certify the target GitLab server, registry or OpenShift cluster.

| Layer | Entry point | Cadence |
| --- | --- | --- |
| Unit and focused integration | `python -m unittest discover -s tests -v` | Every code PR; select the affected class during fixes |
| Local generated pipelines | `npm run test:ci-local` | Every PR and before PyPI publication |
| Disposable GitLab and Runner | `python tools/testing/e2e.py` | Deliberate manual run, release qualification, or opt-in PR label |

## Installation and short feedback loop

Maintainer development setup (public package acquisition is confined to developer/GitHub CI environments):

```sh
python -m pip install . build twine uv setuptools
npm ci --ignore-scripts --no-audit --no-fund
# Install Helm 3.17.3, Git and rsync through your environment's approved mechanism.
```

For air-gapped maintainers, preinstall the same versions from approved internal mirrors and populate npm's cache. None of these commands were added to the consumer runtime bootstrap or image factory.

Run one affected test or scenario:

```sh
python -m unittest discover -s tests -p test_review.py -v
PYTHONPATH=tests python -m unittest test_review.ReviewRegressions.test_reject_empty_matrix -v
python tools/testing/local.py --scenario handoff
python tools/testing/local.py --scenario failed-gate --scenario selection
```

The local harness uses the exact locked `gitlab-ci-local` version. It creates temporary Git repositories, compiles real consumer configurations, executes the installed toolkit code and validates success receipts. `--shell-isolation` and `--no-artifacts-to-source` prevent a shared checkout from satisfying missing artifact transfers. Each run starts with fresh state. The shell fixtures have no dependency-install, image-build, publication or deployment actions.

The shell adapter removes only job `image` fields: gitlab-ci-local 4.75.1 otherwise computes a container CI_PROJECT_DIR even with force-shell-executor. Scripts, rules, needs and artifact declarations stay generated. This path does not test container placement, image availability, runner tags or real GitLab scheduling. No schema-validation option is disabled.

Scenarios: `handoff`, `failed-gate`, `matrix`, and `selection`. A failed-gate scenario passes only when its intentional failure occurs and downstream work produces no success receipts. Every scenario produces a log and the harness writes `.test-results/local/results.json`. Missing required tools or evidence fails the harness.

## Rare server and runner E2E

The architecture follows gitlab-ci-builder's disposable GitLab server, dedicated project runner and observed pipeline outcomes. Defaults pin server, runner and Python base versions in `tools/testing/e2e.py`; versions are inputs for reproducibility, not a claim of a tested support window.

The harness needs Linux, a working Docker daemon, sufficient memory for GitLab (allow at least 4–6 GB), and permission to pull test images. It builds a disposable Python runtime image. Public image/index acquisition here belongs only to maintainer E2E infrastructure.

```sh
python tools/testing/e2e.py --scenario handoff
python tools/testing/e2e.py --scenario merge-request --scenario manual-gate
python tools/testing/e2e.py
```

It boots a loopback-bound GitLab, creates a temporary API token, seeds private fixture projects, registers tagged project-scoped Docker runners, creates commits/MRs and polls actual pipeline/job outcomes. Runner jobs do not receive the administrator token. It inspects downloaded receipts and job traces. `manual-gate` is a supplemental native GitLab approval probe, not a Helm deployment test. On exit it removes its own containers, runtime image and configuration volume. Workflow cleanup also handles interruption.

The six cases are the four local scenarios plus real merge-request pipeline creation and manual gate play. Evidence is written to `.test-results/e2e/`, including the tested commit and image version. No fixture publishes to a real registry or deploys to a cluster. Buildah, OpenShift, TLS admission, registry publication, release-tag automation and Bun/pnpm runtime matrices still require their own live qualification.

GitHub **GitLab E2E** runs only by workflow dispatch, or when a same-repository PR carries `run-gitlab-e2e`. Adding the label allows an unmerged workflow to prove itself; removing it prevents subsequent commits from booting GitLab. The workflow supports one selected scenario, cancels superseded runs, and uploads evidence on failure. There is no nightly schedule and no automatic broad GitLab version matrix.

Before a component release, run the appropriate live qualification on the exact commit and retain its evidence. Unit expectations must be independently specified. Do not rewrite recorded/expected behavior automatically after a failure or retry tests until they turn green.

## Review regressions and release checks

`test_review.py` covers job-name collisions, empty matrices, contextual artifact selection, overlapping deployments, complete versus partial release tags, shell state, shallow Git history and Git dependency subdirectories. Helm tests remain locally optional for developers without Helm, but both fast GitHub CI and PyPI publication install Helm explicitly and check its presence before discovery.

Full local verification:

```sh
python scripts/sync_embedded.py --check
python scripts/sync_authoring_skill.py --check
helm version --short
python -m unittest discover -s tests -v
python tests/integration_uv.py
helm lint charts/generic-app -f examples/helm-values.yaml --strict
npm run test:ci-local
python -m build
python -m twine check --strict dist/*
```
