"""Execution of revision-one workflows using installed internal tools."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import tarfile
import zipfile
from email.parser import BytesParser
import urllib.error
import urllib.parse
import urllib.request
from generic_ci.runtime import (load_config, identity, write_json, path_in, require_receipt,
                                receipt_path, collect, materialize, commands)
from generic_ci.dependencies import resolve_candidates
from generic_ci.config import allowed_url
from .ecosystems import environment, read_version, extract_version, version_key


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def matches(tag, release):
    return bool(release and re.fullmatch(re.escape(release['tag']).replace(re.escape('{version}'), '.+'), tag))


def make_plan(data, root, expected):
    for path, sha in data['sources'].items():
        if hashlib.sha256(path_in(root, path).read_bytes()).hexdigest() != sha:
            raise ValueError(f'{path} changed; render and commit the pipeline again')
    projects = data['pipeline']['projects']
    source, tag = os.environ.get('CI_PIPELINE_SOURCE'), os.environ.get('CI_COMMIT_TAG', '')
    event = 'release' if tag else {'merge_request_event': 'merge-request', 'push': 'push', 'web': 'manual', 'schedule': 'schedule'}.get(source)
    if event is None:
        raise ValueError('unsupported workflow event')
    changed = None
    base = os.environ.get('CI_MERGE_REQUEST_DIFF_BASE_SHA') or os.environ.get('CI_COMMIT_BEFORE_SHA', '')
    if event in {'push', 'merge-request'} and re.fullmatch('[a-f0-9]{40}', base) and set(base) != {'0'}:
        try:
            changed = git(root, 'diff', '--name-only', '--no-renames', base, 'HEAD').splitlines()
        except subprocess.CalledProcessError:
            pass  # Missing shallow baseline conservatively selects everything.
    selected = set(projects)
    if changed is not None and not set(changed).intersection(data['sources']):
        import fnmatch
        selected = {name for name, p in projects.items() if any(
            p['path'] == '.' or f == p['path'] or f.startswith(p['path'].rstrip('/') + '/') or
            any(fnmatch.fnmatch(f, pat) for pat in p['watch']) or f in {'uv.lock', 'package-lock.json', 'pnpm-lock.yaml', 'bun.lock', 'pyproject.toml', 'package.json', 'pnpm-workspace.yaml'}
            for f in changed)}
    direct = set(selected)
    if event == 'release':
        selected = {name for name, p in projects.items() if matches(tag, p['release'])}
        direct = set(selected)
        if not selected:
            raise ValueError('tag matches no project release')
    else:
        while True:
            more = selected | {name for name, p in projects.items() if set(p['depends_on']) & selected}
            if more == selected:
                break
            selected = more
    candidates = resolve_candidates(os.environ, data['platform']['allowed_hosts'], projects)
    if candidates and event == 'release':
        raise ValueError('candidate overrides are forbidden in release workflows')
    for c in candidates:
        selected.update(c['projects'])
    # Complete deployments and fresh previews need a full image baseline.
    for deployment in data['pipeline']['deployments'].values():
        owners = {binding['from_'].split('.')[0] for binding in deployment['images']}
        if event in deployment['workflows'] and owners & selected and (event == 'merge-request' or deployment['update'] == 'complete'):
            selected.update(owners)
    # Artifact producer closure is separate from change propagation.
    while True:
        more = selected | {data['nodes'][dep]['project'] for n in data['nodes'].values()
                           if n['event'] == event and n['project'] in selected for dep in n['needs']
                           if data['nodes'][dep]['project'] is not None}
        if more == selected:
            break
        selected = more
    plan = {**identity(expected), 'event': event, 'selected': sorted(selected), 'direct': sorted(direct), 'candidates': candidates}
    write_json(root / '.ci-out/plan.json', plan)
    print(json.dumps(plan, indent=2))


def api(data, method, path, payload=None, missing=False):
    base = os.environ['CI_API_V4_URL']
    allowed_url(base, data['platform']['allowed_hosts'])
    url = base.rstrip('/') + '/projects/' + urllib.parse.quote(os.environ['CI_PROJECT_ID'], safe='') + path
    token = os.environ.get('TOOLKIT_RELEASE_TOKEN')
    if not token:
        raise ValueError('TOOLKIT_RELEASE_TOKEN must be a protected project access token with API access')
    request = urllib.request.Request(url, method=method, headers={'PRIVATE-TOKEN': token, 'Content-Type': 'application/json'},
                                     data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if missing and error.code == 404:
            return None
        raise ValueError(f'GitLab API {method} failed with HTTP {error.code}') from None


def validate_version(project, root, plan):
    release = project['release']
    directory = path_in(root, project['path'])
    current = read_version(directory, release)
    is_node = release['version']['file'].endswith('.json')
    tag = release['tag'].format(version=current)
    if plan['event'] == 'release':
        if tag != os.environ['CI_COMMIT_TAG']:
            raise ValueError(f'release tag must be {tag}')
        return current
    target = os.environ.get('CI_MERGE_REQUEST_TARGET_BRANCH_NAME')
    if target and release['require_bump']:
        subprocess.run(['git', 'fetch', '--no-tags', 'origin', f'+refs/heads/{target}:refs/ci/version-base'], cwd=root, check=True)
        file = (Path(project['path']) / release['version']['file']).as_posix()
        result = subprocess.run(['git', 'show', 'refs/ci/version-base:' + file], cwd=root, text=True, capture_output=True)
        if result.returncode:
            # New file is legitimate only when absent from a successfully fetched tree.
            exists = git(root, 'ls-tree', 'refs/ci/version-base', '--', file)
            if exists:
                raise ValueError('cannot read target-branch version')
        else:
            previous = extract_version(result.stdout, release['version'])
            if version_key(current, is_node) <= version_key(previous, is_node):
                raise ValueError(f'bump {file}: {current} must be greater than target version {previous}')
    subprocess.run(['git', 'fetch', '--tags', 'origin'], cwd=root, check=True)
    for existing in git(root, 'tag', '--list').splitlines():
        match = re.fullmatch(re.escape(release['tag']).replace(re.escape('{version}'), '(.+)'), existing)
        if match:
            try:
                old = version_key(match[1], is_node)
            except ValueError:
                continue
            if version_key(current, is_node) <= old:
                raise ValueError(f'version {current} must exceed released version {match[1]}')
    return current


def create_release(data, project, root):
    release = project['release']
    if os.environ.get('CI_COMMIT_REF_PROTECTED') != 'true' or os.environ.get('CI_COMMIT_BRANCH') != release['create']['branch']:
        raise ValueError('release creation requires the configured protected branch')
    current = read_version(path_in(root, project['path']), release)
    tag = release['tag'].format(version=current)
    ref = '/repository/tags/' + urllib.parse.quote(tag, safe='')
    existing = api(data, 'GET', ref, missing=True)
    if existing:
        if existing['commit']['id'] != os.environ['CI_COMMIT_SHA']:
            raise ValueError('release tag already points to another commit; it will not be moved')
        return {'tag': tag, 'already_exists': True}
    validate_version(project, root, {'event': 'push'})
    api(data, 'POST', '/repository/tags', {'tag_name': tag, 'ref': os.environ['CI_COMMIT_SHA']})
    return {'tag': tag, 'commit': os.environ['CI_COMMIT_SHA']}


def build_image(data, node, root, out, plan):
    project = data['pipeline']['projects'][node['project']]
    settings = node['settings']
    candidates = [c for c in plan['candidates'] if node['project'] in c['projects']]
    if candidates and (not settings['dependency_bundle'] or project['python'] is None):
        raise ValueError('candidate image builds require the Python dependency-bundle contract; Node candidate images are not supported yet')
    release = plan['event'] == 'release'
    if release and os.environ.get('CI_COMMIT_REF_PROTECTED') != 'true':
        raise ValueError('release image push requires protected tag')
    repository = settings['repository'] or data['platform']['registries']['containers' if release else 'previews'].rstrip('/') + '/' + node['project']
    if not release and settings['repository']:
        repository = data['platform']['registries']['previews'].rstrip('/') + '/' + node['project']
    allowed_url('https://' + repository.split('/')[0], data['platform']['allowed_hosts'])
    version = read_version(path_in(root, project['path']), project['release']) if project['release'] else None
    tag = version if release else 'ci-' + os.environ['CI_PIPELINE_ID'] + '-' + os.environ['CI_COMMIT_SHA'][:12]
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag):
        raise ValueError('version is not a valid container tag; configure a compatible version')
    directory = path_in(root, project['path'])
    image = repository + ':' + tag
    args = ['buildah', 'build', '--iidfile', str(out / 'iid'), '--tag', image,
            '--file', str(path_in(directory, settings['dockerfile']))]
    for key, value in settings['build_args'].items():
        args += ['--build-arg', key + '=' + value]
    for key, variable in settings['secrets'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key):
            raise ValueError('invalid build secret identifier')
        source = Path(os.environ[variable])
        if not source.is_file() or ',' in str(source):
            raise ValueError('build secret must reference a file-type variable')
        args += ['--secret', f'id={key},src={source}']
    if settings['target']:
        args += ['--target', settings['target']]
    if settings['dependency_bundle']:
        if project['python'] is None:
            raise ValueError('dependency-bundle currently supports Python wheelhouses')
        from generic_ci.runtime import bundle_dependencies
        bundle = out / 'dependency-bundle'; bundle.mkdir(exist_ok=True)
        with environment(project, {}, root, node['project'], plan['candidates'], data['platform']['allowed_hosts'], out) as resolved:
            bundle_dependencies(resolved, directory, bundle, data['platform']['allowed_hosts'])
        args += ['--build-context', 'ci-dependencies=' + str(bundle)]
    args += [str(path_in(directory, settings['context']))]
    try:
        subprocess.run(args, cwd=root, check=True)
        subprocess.run(['buildah', 'push', '--digestfile', str(out / 'digest'), image, 'docker://' + image], cwd=root, check=True)
        value = (out / 'digest').read_text().strip()
        if not re.fullmatch('sha256:[a-f0-9]{64}', value):
            raise ValueError('Buildah push did not return a registry digest')
        return {'image': {'repository': repository, 'tag': tag, 'digest': value, 'version': version, 'pushed': True}}
    finally:
        if (out / 'iid').is_file():
            subprocess.run(['buildah', 'rmi', (out / 'iid').read_text().strip()], capture_output=True)


def package_build(project, root, out, directory=None):
    directory = directory or path_in(path_in(root, project['path']), project['package']['directory'])
    dist = out / 'dist'
    dist.mkdir(exist_ok=True)
    if project['python'] is not None:
        subprocess.run(['uv', 'build', '--project', str(directory), '--no-sources', '--out-dir', str(dist)], cwd=directory, check=True)
        files = list(dist.glob('*.whl')) + list(dist.glob('*.tar.gz'))
        if not list(dist.glob('*.whl')) or not list(dist.glob('*.tar.gz')):
            raise ValueError('package build must produce wheel and sdist')
        subprocess.run([sys.executable, '-m', 'twine', 'check', *map(str, files)], check=True)
    elif project['node'] is not None:
        # npm pack handles npm-compatible output regardless of installation manager.
        subprocess.run(['npm', 'pack', '--pack-destination', str(dist)], cwd=directory, check=True)
        files = list(dist.glob('*.tgz'))
        if len(files) != 1:
            raise ValueError('npm pack must produce exactly one package')
    else:
        raise ValueError('package build requires Python or Node settings')
    # Validate the actual archive metadata, not just the source manifest.
    expected_meta = tomllib.loads((directory / 'pyproject.toml').read_text())['project'] if project['python'] is not None else json.loads((directory / 'package.json').read_text())
    for file in files:
        if file.suffix == '.whl':
            with zipfile.ZipFile(file) as archive:
                members = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
                if len(members) != 1:
                    raise ValueError('wheel requires exactly one METADATA file')
                metadata = BytesParser().parsebytes(archive.read(members[0]))
                built_name, built_version = metadata['Name'], metadata['Version']
        else:
            with tarfile.open(file) as archive:
                if project['node'] is not None:
                    metadata = json.load(archive.extractfile('package/package.json'))
                    built_name, built_version = metadata['name'], metadata['version']
                else:
                    members = [m for m in archive.getmembers() if len(Path(m.name).parts) == 2 and m.name.endswith('/PKG-INFO')]
                    if len(members) != 1:
                        raise ValueError('sdist requires root PKG-INFO')
                    metadata = BytesParser().parsebytes(archive.extractfile(members[0]).read())
                    built_name, built_version = metadata['Name'], metadata['Version']
        normalize = lambda s: re.sub(r'[-_.]+', '-', s).lower()
        if normalize(built_name) != normalize(expected_meta['name']) or built_version != expected_meta['version']:
            raise ValueError('built package identity differs from source metadata')
    return {'package': {'name': expected_meta['name'], 'version': expected_meta['version']}, 'files': [{'path': f.relative_to(root).as_posix(), 'artifact': f.relative_to(root).as_posix(),
                       'sha256': hashlib.sha256(f.read_bytes()).hexdigest()} for f in files]}


def publish(data, node, project, root, records, plan):
    from .publication import (check_development_context, destination, release_channel, snapshot_version)
    development = node['settings'].get('channel') == 'development'
    if development:
        check_development_context(plan)
        current = None
    else:
        if os.environ.get('CI_COMMIT_REF_PROTECTED') != 'true' or plan['candidates'] or plan['event'] != 'release':
            raise ValueError('publishing requires a protected release without candidate dependencies')
        current = validate_version(project, root, plan)
    directory = path_in(root, project['path'])
    if node['settings']['publish_package']:
        package_dir = path_in(directory, project['package']['directory'])
        target = destination(project, package_dir, data['platform']['allowed_hosts'], development)
        packages = [r for r in records if r['action'] == 'package' and r.get('project') == node['project']]
        files = [path_in(root, f['artifact']) for r in packages for f in r['files']]
        if len(packages) != 1 or not files:
            raise ValueError('missing unique project package artifacts')
        if development:
            meta = tomllib.loads((package_dir / 'pyproject.toml').read_text())['project'] if project['python'] is not None else json.loads((package_dir / 'package.json').read_text())
            current = snapshot_version(meta['version'], project['node'] is not None)
            if packages[0].get('publication') != {'channel': 'development', 'destination': target}:
                raise ValueError('development artifact destination differs from publication configuration')
        if packages[0]['package']['version'] != current:
            raise ValueError('built package version differs from publication version')
        channel = 'dev' if development else release_channel(current, project['node'] is not None)
        if project['python'] is not None:
            subprocess.run(['uv', 'publish', '--index', target['index'], '--publish-url', target['url'], '--check-url', target['check_url'], *map(str, files)], cwd=package_dir, check=True)
        else:
            for file in files:
                with tarfile.open(file) as archive:
                    packed = json.load(archive.extractfile('package/package.json'))
                config = packed.get('publishConfig', {})
                channel = config.get('tag', channel)
                subprocess.run(['npm', 'publish', str(file), '--registry', target['url'], '--tag', channel, '--ignore-scripts'], cwd=package_dir, check=True)
        if development:
            return {'version': current, 'publication': {'channel': 'development', 'destination': target}}
    tag = project['release']['tag'].format(version=current)
    existing = api(data, 'GET', '/releases/' + urllib.parse.quote(tag, safe=''), missing=True)
    if not existing:
        api(data, 'POST', '/releases', {'tag_name': tag, 'name': tag, 'description': 'Published by verified pipeline ' + os.environ['CI_PIPELINE_ID']})
    return {'version': current, 'tag': tag}


def run_job(data, key, root, expected):
    node = data['nodes'][key]
    out = root / '.ci-out' / key
    out.mkdir(parents=True, exist_ok=True)
    receipt_path(root, key).unlink(missing_ok=True)
    if node['action'] == 'stop':
        from .helm import deploy
        return deploy(data, node, root, out, [], {}, stop=True)
    plan = json.loads((root / '.ci-out/plan.json').read_text())
    if any(plan.get(k) != v for k, v in identity(expected).items()):
        raise ValueError('stale plan')
    if plan['event'] != node['event']:
        raise ValueError('job event does not match plan')
    if node['project'] and not node['deployment'] and node['project'] not in plan['selected']:
        print('Project not selected; no success receipt emitted')
        return
    if node['deployment']:
        bound = {i['from_'].split('.')[0] for i in data['pipeline']['deployments'][node['deployment']]['images']}
        if not bound.intersection(plan['selected']):
            print('Deployment unaffected; no success receipt emitted')
            return
    records = []
    for upstream in node['needs']:
        producer = data['nodes'][upstream]
        deployment = data['pipeline']['deployments'].get(node['deployment'])
        if deployment and deployment['update'] == 'partial' and producer['action'] == 'container' and producer['project'] not in plan['selected']:
            continue
        records.append(require_receipt(root, upstream, expected))
    materialize(root, records)
    project = data['pipeline']['projects'].get(node['project'])
    action = node['action']
    result = {**identity(expected), 'status': 'passed', 'action': action, 'project': node['project'], 'files': []}
    if action in {'check', 'application'}:
        settings = node['settings']
        directory = path_in(root, project['path'])
        environment_vars = {'DEPLOYMENT_URL': r['url'] for r in records if 'url' in r}
        with environment(project, settings, root, node['project'], plan['candidates'], data['platform']['allowed_hosts'], out) as record:
            result['dependencies'] = {k: v for k, v in record.items() if k != 'interpreter'}
            commands(settings['script'], directory, 'sh', environment_vars)
            result['files'] = collect(root, directory, settings.get('outputs', []), out)
    elif action == 'approve':
        pass
    elif action == 'version':
        # Consumer retesting does not require bumping the consumer's version.
        if node['project'] not in plan['direct'] and plan['event'] != 'release':
            result['version'] = read_version(path_in(root, project['path']), project['release'])
        else:
            result['version'] = validate_version(project, root, plan)
    elif action == 'create-release':
        if node['project'] not in plan['direct']:
            print('No direct project changes; no release created')
            return
        result.update(create_release(data, project, root))
    elif action == 'container':
        result.update(build_image(data, node, root, out, plan))
    elif action == 'package':
        with environment(project, {}, root, node['project'], plan['candidates'], data['platform']['allowed_hosts'], out):
            if node['settings'].get('channel') == 'development':
                from .publication import check_development_context, destination, rewrite_snapshot
                check_development_context(plan)
                package_dir = path_in(path_in(root, project['path']), project['package']['directory'])
                target = destination(project, package_dir, data['platform']['allowed_hosts'], True)
                with tempfile.TemporaryDirectory(prefix='generic-ci-package-') as temp:
                    # Preserve workspace layout, generated files and prepared dependencies.
                    # Only the copy's manifest is rewritten, including on build failure.
                    copied = Path(temp) / 'source'
                    shutil.copytree(root, copied, symlinks=True, ignore=shutil.ignore_patterns('.git', '.ci-out'))
                    copied_dir = path_in(path_in(copied, project['path']), project['package']['directory'])
                    if (copied_dir / ('package.json' if project['node'] is not None else 'pyproject.toml')).is_symlink():
                        raise ValueError('development package manifest cannot be a symlink')
                    rewrite_snapshot(copied_dir, project['node'] is not None, target)
                    result.update(package_build(project, root, out, copied_dir))
                result['publication'] = {'channel': 'development', 'destination': target}
            else:
                result.update(package_build(project, root, out))
    elif action == 'publish':
        result.update(publish(data, node, project, root, records, plan))
    elif action == 'deploy':
        from .helm import deploy
        result.update(deploy(data, node, root, out, records, plan))
    else:
        raise ValueError(f'unknown action {action}')
    write_json(receipt_path(root, key), result)


def main():
    try:
        expected = sys.argv[-1]
        data = load_config(expected)
        if data.get('format') != 'workflows-v1':
            raise ValueError('not a workflow execution configuration')
        root = Path(os.environ.get('CI_PROJECT_DIR', '.')).resolve()
        if sys.argv[1] == 'plan':
            make_plan(data, root, expected)
        elif sys.argv[1] == 'run':
            run_job(data, sys.argv[2], root, expected)
        else:
            raise ValueError('expected plan or run')
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f'generic-ci: {error}', file=sys.stderr)
        raise SystemExit(1) from error

if __name__ == '__main__':
    main()
