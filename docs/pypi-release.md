# Publishing this toolkit to PyPI — revision one

The distribution name is `generic-gitlab-cicd`, matching the configured PyPI project. The shell command remains `generic-ci`, and the Python package remains `generic_ci`.

The GitHub Actions workflow `.github/workflows/publish.yml` tests this repository, builds a wheel and source distribution, checks their metadata, and publishes the resulting artifacts through PyPI Trusted Publishing. Only the publish job requests an OIDC token. No PyPI API token or password is needed. This workflow runs on GitHub-hosted infrastructure; consumer GitLab pipelines continue to use their own internal image/index configuration.

Configure the PyPI Trusted Publisher with these exact values:

| Setting | Value |
| --- | --- |
| PyPI project | `generic-gitlab-cicd` |
| GitHub owner | `Heknon` |
| GitHub repository | `generic-gitlab-cicd` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

The workflow filename is the basename, not `.github/workflows/publish.yml`. If your existing publisher uses another filename or environment, align it with these values. Repository code cannot inspect your private PyPI publisher settings.

To publish:

1. Set a new version in `pyproject.toml` and `generic_ci/__init__.py`, commit and push.
2. Publish a GitHub release with a matching tag, for example `v0.3.1`. Both `0.3.1` and `v0.3.1` are accepted.
3. Watch **Actions → Publish to PyPI**. The upload runs only after tests and package checks succeed.

Alternatively, use **Actions → Publish to PyPI → Run workflow**, selecting the branch or tag to publish. A manual run publishes the version already present in that revision; it does not generate a version or create a GitHub release. A push to master alone does not publish.

PyPI versions cannot be overwritten. The workflow does not hide existing-version errors with `skip-existing`; inspect the package state before retrying an upload.

After publication, install with:

```sh
pip install generic-gitlab-cicd
# Or install the CLI in an isolated uv tool environment:
uv tool install generic-gitlab-cicd
```

[PyPI's Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/) describes the OIDC mechanism. Adding this workflow does not itself publish a package or verify the configured publisher; an actual release/manual run provides that verification.
