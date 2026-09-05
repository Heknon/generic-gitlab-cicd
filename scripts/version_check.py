"""Static PEP 621 versions: compare target branch and validate release tags."""
import os
from pathlib import Path
import subprocess
import tomllib
from packaging.version import Version


def get_version(raw):
    data = tomllib.loads(raw)
    project = data.get('project', {})
    if 'version' in project.get('dynamic', []) or 'version' not in project:
        raise ValueError('Static project.version required; configure a custom check for SCM/dynamic versions')
    version = Version(project['version'])
    if version.local:
        raise ValueError('Local version suffixes are not supported for public releases')
    return version


def main():
    path = Path(os.environ.get('COMPONENT_PYPROJECT','pyproject.toml'))
    current = get_version(path.read_text())
    tag = os.environ.get('CI_COMMIT_TAG','')
    if tag:
        prefix = os.environ.get('COMPONENT_TAG_PREFIX','v')
        if not tag.startswith(prefix) or Version(tag[len(prefix):]) != current:
            raise ValueError(f'Tag must match {prefix}{current}')
        print(f'Tag matches package version {current}')
        return
    target = os.environ.get('CI_MERGE_REQUEST_TARGET_BRANCH_NAME') or os.environ.get('CI_DEFAULT_BRANCH','main')
    if os.environ.get('CI_COMMIT_BRANCH') == target:
        print(f'Default branch version: {current}; bump enforcement occurs before merge')
        return
    subprocess.run(['git','fetch','--no-tags','origin',f'+refs/heads/{target}:refs/ci/version-base'],check=True)
    result = subprocess.run(['git','show',f'refs/ci/version-base:{path.as_posix()}'],text=True,capture_output=True)
    if result.returncode:
        if os.environ.get('COMPONENT_ALLOW_NEW') == 'enabled-true':
            # Distinguish missing file from invalid fetch/object or other git failures.
            exists = subprocess.run(['git','cat-file','-e','refs/ci/version-base^{commit}'])
            listing = subprocess.check_output(['git','ls-tree','-r','--name-only','refs/ci/version-base','--',path.as_posix()],text=True)
            if exists.returncode == 0 and not listing.strip():
                print(f'New package starts at {current}')
                return
        raise ValueError('Cannot read baseline package version')
    previous = get_version(result.stdout)
    if current <= previous:
        raise ValueError(f'Bump project.version above {previous}; got {current}')
    print(f'Version bump accepted: {previous} -> {current}')

if __name__ == '__main__':
    main()
