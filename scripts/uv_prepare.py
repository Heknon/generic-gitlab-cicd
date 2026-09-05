"""Resolve candidate dependencies to immutable Git commits, sync, restore manifests."""
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
import tomlkit
import tomllib


def resolve_override(item):
    name, repo, ref = (item.get(k, '') for k in ('package', 'repo', 'ref'))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        raise ValueError('Invalid distribution package name')
    url = urlsplit(repo)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use a credential-free HTTPS Git repository URL')
    if not ref or ref.startswith('-') or re.search(r'[\s~^:?*\[\\]', ref):
        raise ValueError('Invalid Git ref')
    sub = item.get('subdirectory', '')
    if sub and (not re.fullmatch(r'[A-Za-z0-9_./-]+', sub) or '..' in sub.split('/') or sub.startswith('/')):
        raise ValueError('Invalid package subdirectory')
    if re.fullmatch(r'[a-fA-F0-9]{40}', ref):
        sha = ref.lower()
    else:
        wanted = [ref] if ref.startswith('refs/') else ['refs/heads/'+ref, 'refs/tags/'+ref]
        result = subprocess.run(['git', 'ls-remote', '--exit-code', repo, *wanted, *[x+'^{}' for x in wanted]], capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Cannot resolve dependency ref; check ref, credentials and job-token allowlist')
        refs = dict(line.split()[::-1] for line in result.stdout.splitlines())
        matches = [r for r in wanted if r in refs]
        if len(matches) != 1:
            raise ValueError('Ambiguous ref; use refs/heads/... or refs/tags/...')
        sha = refs.get(matches[0]+'^{}', refs[matches[0]])
    requirement = f'{name} @ git+{repo}@{sha}' + (f'#subdirectory={sub}' if sub else '')
    return requirement, {'package': name, 'repo': repo, 'requested_ref': ref, 'commit': sha, 'subdirectory': sub}


def prepare_here(member=None):
    overrides = json.loads((os.environ.get('CI_DEPENDENCY_OVERRIDES') or '[]'))
    if not isinstance(overrides, list):
        raise ValueError('CI_DEPENDENCY_OVERRIDES must be a JSON array')
    single = [os.environ.get('CI_DEPENDENCY_'+k, '') for k in ('REPO', 'REF', 'PACKAGE')]
    if any(single):
        if not all(single):
            raise ValueError('Provide repository, ref and package together')
        overrides.append(dict(zip(('repo','ref','package'), single)))
    project = Path('pyproject.toml')
    lock = Path('uv.lock')
    if not lock.is_file():
        raise ValueError('Commit uv.lock; normal validation uses uv sync --locked')
    if not overrides:
        subprocess.run(['uv','sync','--locked','--all-groups'] + (['--package', member] if member else []), check=True)
        return
    original = project.read_bytes()
    original_lock = lock.read_bytes()
    provenance = []
    requirements = []
    names = set()
    for item in overrides:
        req, record = resolve_override(item)
        normalized = re.sub(r'[-_.]+','-',record['package']).lower()
        if normalized in names:
            raise ValueError('Duplicate package override')
        names.add(normalized)
        requirements.append(req)
        provenance.append(record)
    data = tomlkit.parse(original.decode())
    uv = data.setdefault('tool', {}).setdefault('uv', {})
    existing = uv.get('override-dependencies', [])
    # Avoid conflicting existing overrides for the same distribution.
    from packaging.requirements import Requirement
    existing = [x for x in existing if re.sub(r'[-_.]+','-',Requirement(x).name).lower() not in names]
    uv['override-dependencies'] = existing + requirements
    sources = uv.get('sources', {})
    for key in list(sources):
        if re.sub(r'[-_.]+','-',key).lower() in names:
            del sources[key]
    try:
        project.write_text(tomlkit.dumps(data))
        subprocess.run(['uv','sync','--all-groups'] + (['--package', member] if member else []), check=True)
        # Verify actual installed source and commit, including transitive overrides.
        code = "import importlib.metadata as m,json,sys; print(json.dumps({n:json.loads(m.distribution(n).read_text('direct_url.json') or '{}') for n in sys.argv[1:]}))"
        interpreter = '.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python'
        installed = json.loads(subprocess.check_output([interpreter,'-c',code,*[r['package'] for r in provenance]],text=True))
        for record in provenance:
            if installed[record['package']].get('vcs_info',{}).get('commit_id') != record['commit']:
                raise ValueError('Installed dependency does not match requested commit')
        out = Path(os.environ['CI_PROJECT_DIR']) / os.environ['COMPONENT_PROVENANCE']
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(provenance,indent=2)+'\n')
    finally:
        project.write_bytes(original)
        lock.write_bytes(original_lock)


def main():
    original_directory = Path.cwd().resolve()
    repository = Path(os.environ.get('CI_PROJECT_DIR', original_directory)).resolve()
    owner, member = original_directory, None
    for parent in (original_directory, *original_directory.parents):
        if not parent.is_relative_to(repository):
            break
        manifest = parent / 'pyproject.toml'
        if not manifest.is_file():
            continue
        data = tomllib.loads(manifest.read_text())
        settings = data.get('tool', {}).get('uv', {}).get('workspace')
        if settings:
            members = {p.resolve() for pattern in settings.get('members', []) for p in parent.glob(pattern)}
            excluded = {p.resolve() for pattern in settings.get('exclude', []) for p in parent.glob(pattern)}
            if original_directory in members - excluded:
                owner = parent
                member = tomllib.loads((original_directory / 'pyproject.toml').read_text())['project']['name']
                break
    try:
        os.chdir(owner)
        prepare_here(member)
    finally:
        os.chdir(original_directory)

if __name__ == '__main__':
    main()
