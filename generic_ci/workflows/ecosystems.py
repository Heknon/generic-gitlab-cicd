"""Workspace-aware package preparation and ecosystem version ordering."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tarfile
import shutil
import tomllib
import yaml
from packaging.version import Version
from generic_ci.dependencies import prepared
from generic_ci.runtime import path_in
from generic_ci.config import allowed_url


def read_version(directory, release):
    source = release['version']
    path = path_in(directory, source['file'])
    return extract_version(path.read_text(), source)


def extract_version(text, source):
    data = json.loads(text) if source['file'].endswith('.json') else tomllib.loads(text)
    for key in source['field'].split('.'):
        data = data[key]
    if not isinstance(data, str):
        raise ValueError('version field must be a string')
    version_key(data, source['file'].endswith('.json'))
    return data


def version_key(value, node=False):
    if not node:
        v = Version(value)
        if v.local:
            raise ValueError('release versions cannot have local suffixes')
        return v
    # SemVer precedence: build metadata does not change precedence.
    match = re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?', value)
    if not match:
        raise ValueError(f'invalid SemVer: {value}')
    pre = match[4]
    for sequence in (pre, match[5]):
        if sequence is not None and any(not part for part in sequence.split('.')):
            raise ValueError('empty SemVer identifier')
    if pre and any(part.isdigit() and len(part) > 1 and part.startswith('0') for part in pre.split('.')):
        raise ValueError('numeric SemVer prerelease identifiers cannot have leading zeroes')
    return (int(match[1]), int(match[2]), int(match[3]), pre is None,
            tuple((0, int(part)) if part.isdigit() else (1, part) for part in pre.split('.')) if pre else ())


def node_workspace(directory, root, settings):
    if settings['workspace'] is not None:
        result = path_in(root, settings['workspace'])
        if not directory.is_relative_to(result):
            raise ValueError('Node workspace must contain project directory')
        return result
    for parent in (directory, *directory.parents):
        if not parent.is_relative_to(root):
            break
        manifest = parent / 'package.json'
        if not manifest.is_file():
            continue
        metadata = json.loads(manifest.read_text())
        patterns = metadata.get('workspaces', [])
        if isinstance(patterns, dict):
            patterns = patterns.get('packages', [])
        if (parent / 'pnpm-workspace.yaml').is_file():
            patterns = yaml.safe_load((parent / 'pnpm-workspace.yaml').read_text()).get('packages', [])
        members = {p.resolve() for pattern in patterns if not pattern.startswith('!') for p in parent.glob(pattern)}
        excluded = {p.resolve() for pattern in patterns if pattern.startswith('!') for p in parent.glob(pattern[1:])}
        if directory in members - excluded:
            return parent
    return directory


@contextlib.contextmanager
def environment(project, check, root, name, candidates, hosts, out):
    directory = path_in(root, project['path'])
    if check.get('dependencies') is False:
        yield {'manager': 'none'}
    elif project['python'] is not None:
        policy = {**project['python'], **(check.get('dependencies') or {}), 'manager': 'uv', 'python': os.environ.get('PYTHON_VERSION')}
        with prepared(directory, root, policy, candidates, name, hosts, out) as record:
            yield record
    elif project['node'] is not None:
        settings = project['node']
        manager = settings['package_manager']
        workspace = node_workspace(directory, root, settings)
        lock_name = {'npm': 'package-lock.json', 'pnpm': 'pnpm-lock.yaml', 'bun': 'bun.lock'}[manager]
        lock = workspace / lock_name
        if not lock.is_file():
            raise ValueError(f'commit {lock_name} at Node workspace root; binary Bun lockfiles must be migrated')
        metadata = json.loads((workspace / 'package.json').read_text())
        requested = metadata.get('packageManager')
        if requested:
            match = re.fullmatch(r'(npm|pnpm|bun)@([^+]+)(?:\+.*)?', requested)
            if not match or match[1] != manager:
                raise ValueError('packageManager metadata conflicts with selected manager')
            actual = subprocess.check_output([manager, '--version'], text=True).strip()
            if actual != match[2]:
                raise ValueError(f'prepared image needs {requested}; found {actual}; no automatic tool download')
        selected = [c for c in candidates if name in c['projects']]
        if selected and manager == 'pnpm' and (workspace / 'pnpm-workspace.yaml').is_file():
            pnpm_settings = yaml.safe_load((workspace / 'pnpm-workspace.yaml').read_text())
            if pnpm_settings.get('overrides'):
                raise ValueError('candidate overrides with pnpm-workspace.yaml overrides require reconciliation; refusing ambiguous precedence')
        # Require an explicitly configured internal default registry. Scope-specific registries
        # and lifecycle-script network access additionally need the platform network boundary.
        registry = os.environ.get('NPM_CONFIG_REGISTRY') or os.environ.get('npm_config_registry')
        if not registry:
            result = subprocess.run(['npm', 'config', 'get', 'registry'], cwd=workspace, text=True, capture_output=True, check=True)
            registry = result.stdout.strip()
        allowed_url(registry, hosts)
        original = lock.read_bytes()
        manifests = {p: p.read_bytes() for p in [workspace / 'package.json', directory / 'package.json']}
        candidate_dir = tempfile.TemporaryDirectory()
        env = {**os.environ, 'npm_config_registry': registry, 'NPM_CONFIG_REGISTRY': registry,
               'COREPACK_ENABLE_NETWORK': '0', 'CI': 'true'}
        command = ['npm', 'ci'] if manager == 'npm' else [manager, 'install', '--frozen-lockfile']
        try:
            candidate_files = []
            for index, candidate in enumerate(selected):
                clone = Path(candidate_dir.name) / str(index)
                subprocess.run(['git', 'clone', '--no-checkout', candidate['repository'], str(clone)], check=True)
                subprocess.run(['git', 'checkout', '--detach', candidate['commit']], cwd=clone, check=True)
                actual_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=clone, text=True).strip()
                if actual_sha != candidate['commit']:
                    raise ValueError('candidate checkout identity mismatch')
                candidate_root = path_in(clone, candidate['subdirectory'] or '.')
                package_metadata = json.loads((candidate_root / 'package.json').read_text())
                if package_metadata['name'] != candidate['package']:
                    raise ValueError('candidate package name does not match package.json')
                packed = clone / 'packed'; packed.mkdir()
                # Candidate repository must carry publishable output. Never run unverified
                # prepare scripts with publishing credentials to silently build it.
                subprocess.run(['npm', 'pack', '--ignore-scripts', '--pack-destination', str(packed)], cwd=candidate_root, env=env, check=True)
                archives = list(packed.glob('*.tgz'))
                if len(archives) != 1:
                    raise ValueError('candidate pack must produce exactly one tarball')
                archive = archives[0]
                candidate_files.append((candidate, archive))
                uri = 'file:' + str(archive)
                for path in manifests:
                    document = json.loads(path.read_text())
                    for section in ('dependencies', 'devDependencies', 'optionalDependencies'):
                        if candidate['package'] in document.get(section, {}):
                            document[section][candidate['package']] = uri
                    if path == workspace / 'package.json':
                        owner = document.setdefault('pnpm', {}) if manager == 'pnpm' else document
                        owner.setdefault('overrides', {})[candidate['package']] = uri
                    path.write_text(json.dumps(document, indent=2) + '\n')
            if selected:
                refresh = ['npm', 'install', '--package-lock-only', '--ignore-scripts'] if manager == 'npm' else [manager, 'install', '--lockfile-only', '--ignore-scripts']
                subprocess.run(refresh, cwd=workspace, env=env, check=True)
            resolved_lock = lock.read_bytes()
            subprocess.run(command, cwd=workspace, env=env, check=True)
            if lock.read_bytes() != resolved_lock:
                raise ValueError('frozen install unexpectedly changed lockfile')
            for candidate, archive in candidate_files:
                locations = [parent / 'node_modules' / candidate['package'] for parent in (directory, *directory.parents) if parent.is_relative_to(workspace)]
                installed = next((p.resolve() for p in locations if (p / 'package.json').is_file()), None)
                if installed is None:
                    raise ValueError('candidate is not installed in the consuming project')
                with tarfile.open(archive) as tar:
                    for member in tar.getmembers():
                        if not member.isfile():
                            continue
                        relative = Path(member.name).relative_to('package')
                        target = path_in(installed, str(relative))
                        if not target.is_file() or target.read_bytes() != tar.extractfile(member).read():
                            raise ValueError('installed candidate differs from packed candidate commit')
            yield {'manager': manager, 'workspace': workspace.relative_to(root).as_posix(),
                   'lock_sha256': hashlib.sha256(resolved_lock).hexdigest(), 'candidates': selected}
        finally:
            candidate_dir.cleanup()
            lock.write_bytes(original)
            for path, content in manifests.items():
                path.write_bytes(content)
    else:
        yield {'manager': 'none'}
