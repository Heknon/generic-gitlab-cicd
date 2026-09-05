"""Read-only readiness checks, readable event plans, lint, and reviewed upgrades."""
import difflib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import __version__
from .config import allowed_url, read_yaml
from .runtime import path_in


def explain(data, jobs, event=None, changed=None, tag='', as_json=False, origins=None):
    from .workflows.selection import select
    selection = select(data, event, changed, tag=tag) if event else None
    rows = []
    for name, node in data['nodes'].items():
        if event and node['event'] != event:
            continue
        active = selection is None or (node['deployment'] in selection['deployments'] if node['deployment'] else node['project'] in selection['selected'])
        project = data['pipeline']['projects'].get(node['project'], {})
        rows.append({'job': name, 'selected': active, 'action': node['action'],
                     'directory': project.get('path', '.'), 'script': node['settings'].get('script', []),
                     'needs': node['needs'], 'image': jobs[name]['image'],
                     'manual': any(r.get('when') == 'manual' for r in jobs[name]['rules']),
                     'environment': jobs[name].get('environment'), 'condition': jobs[name]['rules'][0]['if']})
    report = {'origins': origins or {}, 'projects': data['pipeline']['projects'], 'platform': data['platform'], 'compiler': data['version'], 'event': event, 'selection': selection, 'jobs': rows}
    if as_json:
        return json.dumps(report, indent=2) + '\n'
    lines = [f'Pipeline compiled with generic-ci {data["version"]}',
             'Simulation: ' + (event or 'all configured events (no change filtering)')]
    if selection:
        for name, reasons in selection['reasons'].items():
            lines.append(f'  Select {name}: ' + '; '.join(reasons))
    for row in rows:
        lines.append(f'\n{"RUN" if row["selected"] else "SKIP"} {row["job"]}' + (' [manual]' if row['manual'] else ''))
        lines.append(f'  Runtime: {row["image"]}; directory: {row["directory"]}')
        if row['script']:
            lines.extend('  $ ' + command for command in row['script'])
        else:
            lines.append('  Action: ' + row['action'])
        if row['needs']:
            lines.append('  Waits for: ' + ', '.join(row['needs']))
        if row['environment']:
            lines.append('  Environment: ' + row['environment']['name'])
    lines.append('\nSelection assumes matching branch/protected-ref/credential conditions. No commands executed.')
    return '\n'.join(lines) + '\n'


def doctor(data, root, runtime_role=None):
    """Inspect referenced local inputs; never execute application scripts or pull images."""
    from .dependencies import workspace
    from .workflows.ecosystems import node_workspace, read_version
    from .workflows.publication import destination
    findings = []

    def check(label, function):
        try:
            detail = function()
            findings.append({'status': 'ok', 'check': label, 'detail': str(detail or 'present')})
        except (ValueError, OSError, KeyError, TypeError) as error:
            findings.append({'status': 'error', 'check': label, 'detail': str(error)})

    def exists(path, directory=False):
        if not (path.is_dir() if directory else path.is_file()):
            raise ValueError('missing ' + str(path.relative_to(root)))
        return path.relative_to(root)

    for name, p in data['pipeline']['projects'].items():
        directory = path_in(root, p['path'])
        check(name + ': project directory', lambda: exists(directory, True))
        if p['python'] is not None:
            check(name + ': Python manifest', lambda: exists(directory / 'pyproject.toml'))
            check(name + ': Python workspace lock', lambda: exists(workspace(directory, root) / 'uv.lock'))
        if p['node'] is not None:
            check(name + ': Node manifest', lambda: exists(directory / 'package.json'))
            def node_lock():
                owner = node_workspace(directory, root, p['node'])
                return exists(owner / {'npm': 'package-lock.json', 'pnpm': 'pnpm-lock.yaml', 'bun': 'bun.lock'}[p['node']['package_manager']])
            check(name + ': Node workspace lock', node_lock)
        if p['container']:
            check(name + ': Dockerfile', lambda: exists(path_in(directory, p['container']['dockerfile'])))
            check(name + ': container context', lambda: exists(path_in(directory, p['container']['context']), True))
        if p['release']:
            check(name + ': release version', lambda: read_version(directory, p['release']))
        for event, workflow in p['workflows'].items():
            if workflow['publish']:
                development = isinstance(workflow['publish'], dict) and workflow['publish']['channel'] == 'development'
                check(name + ': ' + event + ' publishing destination', lambda: destination(p, path_in(directory, p['package']['directory']), data['platform']['allowed_hosts'], development))
    for name, d in data['pipeline']['deployments'].items():
        if d['chart']['path']:
            check(name + ': chart', lambda: exists(path_in(root, d['chart']['path']) / 'Chart.yaml'))
        for value in d['values']:
            check(name + ': values ' + value, lambda: exists(path_in(root, value)))
    tools = {'control': ['python', 'git', 'bash'], 'python': ['python', 'git', 'bash', 'uv'],
             'node': ['python', 'git', 'bash', 'node', 'npm'], 'bun': ['python', 'git', 'bash', 'bun', 'npm'],
             'helm': ['python', 'git', 'bash', 'helm'], 'builder': ['python', 'git', 'bash', 'buildah']}
    def executable(tool):
        path = shutil.which(tool)
        if not path:
            raise ValueError('not installed')
        return path

    if runtime_role:
        required = set(tool for role, entries in tools.items() if runtime_role in {role, 'all'} for tool in entries)
        if runtime_role in {'node', 'all'} and any((p['node'] or {}).get('package_manager') == 'pnpm' for p in data['pipeline']['projects'].values()):
            required.add('pnpm')
        for tool in sorted(required):
            check('local runtime executable: ' + tool, lambda tool=tool: executable(tool))
    requirements = {'runtime_contract': 'protocol 2 / 0.4.x (Python, Git and Bash required)',
                    'images': data['platform']['images'],
                    'variables': sorted({data['platform']['targets'][d['target']]['kubeconfig_variable'] for d in data['pipeline']['deployments'].values()} |
                                        ({'TOOLKIT_RELEASE_TOKEN'} if any(p['release'] and (p['release']['gitlab'] or p['release']['create']) for p in data['pipeline']['projects'].values()) else set())),
                    'not_verified': ['remote image contents/access', 'registry credentials and CA trust', 'GitLab acceptance', 'cluster permissions and rollout']}
    return {'valid': not any(r['status'] == 'error' for r in findings), 'checks': findings, 'requirements': requirements}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('GitLab lint endpoint redirected; supply its canonical HTTPS URL')


def lint(content, platform, url, project, ref=None, simulate=False):
    url = (url or os.environ.get('CI_API_V4_URL') or '').rstrip('/')
    if not url or not project:
        raise ValueError('lint requires --gitlab-url and --project (ID or group/path)')
    allowed_url(url, platform['allowed_hosts'])
    if not url.endswith('/api/v4'):
        url += '/api/v4'
    token = os.environ.get('GENERIC_CI_GITLAB_TOKEN') or os.environ.get('GITLAB_TOKEN')
    if not token:
        raise ValueError('set GENERIC_CI_GITLAB_TOKEN or GITLAB_TOKEN for CI Lint; tokens are never command arguments')
    payload = {'content': content, 'dry_run': simulate, 'include_jobs': True}
    if ref:
        payload['ref'] = ref
    request = urllib.request.Request(url + '/projects/' + urllib.parse.quote(project, safe='') + '/ci/lint',
        data=json.dumps(payload).encode(), headers={'PRIVATE-TOKEN': token, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError(f'GitLab CI Lint returned HTTP {error.code}; check URL, project and token access') from None
    except urllib.error.URLError:
        raise ValueError('GitLab CI Lint connection failed; check network and trusted CA configuration') from None


def upgrade(root, output, config=None, platform=None, offline=False, source_ref=None, images=(), apply=False):
    """Stage inputs and schemas; show the complete diff; apply with rollback on write error."""
    from .cli import main
    from .sources import source_main
    descriptor = read_yaml(root / 'generic-ci.yml') if (root / 'generic-ci.yml').exists() else None
    delivery_name = config or (descriptor or {}).get('delivery', 'delivery.yml')
    platform_name = platform or (descriptor or {}).get('platform') or 'ci-platform.yml'
    delivery_name = path_in(root, delivery_name).relative_to(root).as_posix()
    platform_name = path_in(root, platform_name).relative_to(root).as_posix()
    config = delivery_name if config else None
    platform = platform_name if platform else None
    output = Path(output).resolve()
    if not output.is_relative_to(root):
        raise ValueError('upgrade output must be within --root')
    output_name = output.relative_to(root)
    originals = {}
    with tempfile.TemporaryDirectory(prefix='generic-ci-upgrade-') as tmp:
        stage = Path(tmp)
        names = {delivery_name, platform_name, 'generic-ci.yml', 'generic-ci.lock.json',
                 '.generic-ci/delivery.schema.json', '.generic-ci/platform.schema.json', str(output_name)}
        for name in names:
            target = path_in(root, name)
            if target.is_file():
                copy = stage / name; copy.parent.mkdir(parents=True, exist_ok=True); copy.write_bytes(target.read_bytes())
        if source_ref:
            if not descriptor:
                raise ValueError('--source-ref requires a source-backed project')
            if offline:
                raise ValueError('source revision updates require acquisition; omit --offline or keep the existing source lock')
            source_main(['update', '--root', str(stage), '--ref', source_ref])
        if images:
            target = stage / platform_name
            overlay = read_yaml(target) if target.exists() else {}
            for binding in images:
                role, separator, image = binding.partition('=')
                if not separator or role not in {'python','node','bun','control','helm','builder'} or not image:
                    raise ValueError('--runtime-image requires ROLE=IMAGE (python/node/bun/control/helm/builder)')
                if role == 'builder':
                    overlay.setdefault('container-builder', {})['image'] = image
                else:
                    overlay.setdefault('images', {})[role] = image
            target.parent.mkdir(parents=True, exist_ok=True); target.write_text(yaml.safe_dump(overlay, sort_keys=False))
            if descriptor:
                staged_descriptor = read_yaml(stage / 'generic-ci.yml'); staged_descriptor['platform'] = platform_name
                (stage / 'generic-ci.yml').write_text(yaml.safe_dump(staged_descriptor, sort_keys=False))
        extra = (['--config', config] if config else []) + (['--platform', platform] if platform else []) + (['--offline'] if offline else [])
        for schema, flag in [('delivery', []), ('platform', ['--platform-schema'])]:
            if main(['schema', *flag, '-o', str(stage / f'.generic-ci/{schema}.schema.json')]):
                raise ValueError('schema refresh failed')
        if main(['render', '--root', str(stage), *extra, '-o', str(stage / output_name)]):
            raise ValueError('upgrade failed validation; original project files were not changed')
        updates = {}
        for staged in sorted(stage.rglob('*')):
            if not staged.is_file():
                continue
            target = root / staged.relative_to(stage)
            if target.is_symlink() or any(p.is_symlink() for p in target.parents):
                raise ValueError('upgrade refuses symlink destinations')
            before = target.read_bytes() if target.is_file() else None
            after = staged.read_bytes()
            if before != after:
                originals[target] = before; updates[target] = after
                name = target.relative_to(root).as_posix()
                print(''.join(difflib.unified_diff((before or b'').decode().splitlines(True), after.decode().splitlines(True), fromfile='old/' + name, tofile='new/' + name)), end='')
        print(f'{len(updates)} files would change. Runtime contract: 0.4.x / protocol 2. Runtime images are not built or verified by upgrade.')
        if not apply:
            print('Preview only. Re-run with --apply after reviewing inputs and compatible runtime images.')
            return bool(updates)
        written = []
        try:
            for target, content in updates.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + '.generic-ci-tmp')
                created = False
                try:
                    with temporary.open('xb') as stream:
                        created = True
                        stream.write(content)
                    temporary.replace(target)
                finally:
                    if created:
                        temporary.unlink(missing_ok=True)
                written.append(target)
        except OSError:
            for target in reversed(written):
                if originals[target] is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(originals[target])
            raise
        print('Upgrade applied. Review and commit the diff; run doctor and GitLab CI Lint.')
        return bool(updates)
