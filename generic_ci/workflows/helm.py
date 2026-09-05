"""Helm image mapping with an explicit baseline for partial shared releases."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import yaml
from generic_ci.runtime import path_in, write_json
from generic_ci.compiler import digest


def get_value(data, path):
    for key in path.split('.'):
        if not isinstance(data, dict) or key not in data:
            raise ValueError(f'previous release has no image value at {path}; use complete deployment')
        data = data[key]
    return data


def set_value(data, path, value):
    parts = path.split('.')
    for key in parts[:-1]:
        data = data.setdefault(key, {})
    data[parts[-1]] = value


def overlay_images(bindings, images, previous, partial):
    overlay = {}
    for binding in bindings:
        project = binding['from_'].split('.')[0]
        image = images.get(project)
        for field, path in binding['set'].items():
            if path is None:
                continue
            if image is not None:
                value = image[field]
            elif partial:
                value = get_value(previous, path)
            else:
                raise ValueError(f'missing image build for {project}')
            if not isinstance(value, str) or not value:
                raise ValueError(f'image value at {path} must be a nonempty string')
            set_value(overlay, path, value)
    return overlay


def deploy(data, node, root, out, records, plan, stop=False):
    settings, infra = node['settings'], data['platform']
    target = infra['targets'][settings['target']]
    preview = node['event'] == 'merge-request'
    if not preview and target['production'] and os.environ.get('CI_COMMIT_REF_PROTECTED') != 'true':
        raise ValueError('production requires a protected ref')
    if not preview and plan.get('candidates'):
        raise ValueError('candidate runs cannot update persistent deployments')
    release = target['release'] or node['deployment']
    if preview:
        release += '-mr-' + os.environ['CI_PROJECT_ID'] + '-' + os.environ['CI_MERGE_REQUEST_IID']
    if len(release) > 53:
        release = release[:40].rstrip('-') + '-' + hashlib.sha256(release.encode()).hexdigest()[:12]
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', release):
        raise ValueError('invalid Helm release name')
    namespace = os.path.expandvars(target['namespace'])
    env = {**os.environ, 'KUBECONFIG': os.environ[target['kubeconfig_variable']]}
    url = os.path.expandvars(target['url'])
    if stop:
        subprocess.run(['helm', 'uninstall', release, '--namespace', namespace, '--ignore-not-found', '--wait'], env=env, check=True)
        return {'release': release, 'url': url}
    chart = settings['chart']
    with tempfile.TemporaryDirectory() as tmp:
        if chart['path']:
            chart_path = path_in(root, chart['path'])
            chart_hash = digest({str(f.relative_to(chart_path)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(chart_path.rglob('*')) if f.is_file()})
        else:
            args = ['helm', 'pull', chart['oci'] or chart['name'], '--version', chart['version'], '--destination', tmp]
            if chart['repository']:
                args += ['--repo', chart['repository']]
            # Registry config and CA are injected as environment/file configuration.
            if os.environ.get('TOOLKIT_HELM_CA_FILE'):
                args += ['--ca-file', os.environ['TOOLKIT_HELM_CA_FILE']]
            if chart['repository'] and os.environ.get('TOOLKIT_HELM_USERNAME'):
                args += ['--username', os.environ['TOOLKIT_HELM_USERNAME'], '--password', os.environ['TOOLKIT_HELM_PASSWORD']]
            subprocess.run(args, env=env, check=True)
            archives = list(Path(tmp).glob('*.tgz'))
            if len(archives) != 1:
                raise ValueError('Helm pull must produce exactly one chart archive')
            chart_path = archives[0]
            chart_hash = hashlib.sha256(chart_path.read_bytes()).hexdigest()
        value_files = [path_in(root, p) for p in settings['values']]
        baseline = digest({'chart': chart_hash, 'values': [hashlib.sha256(f.read_bytes()).hexdigest() for f in value_files], 'bindings': settings['images']})
        listing = json.loads(subprocess.check_output(['helm', 'list', '--all', '--namespace', namespace, '--filter', '^' + re.escape(release) + '$', '--output', 'json'], env=env, text=True))
        previous = {}
        old_meta = {}
        if listing:
            if len(listing) != 1 or listing[0]['status'] != 'deployed':
                raise ValueError('previous Helm release is not deployed; reconcile failed/pending state first')
            previous = json.loads(subprocess.check_output(['helm', 'get', 'values', release, '--namespace', namespace, '--output', 'json'], env=env, text=True)) or {}
            old_meta = previous.get('genericCiMetadata', {})
        partial = settings['update'] == 'partial'
        if partial and listing and old_meta.get('baseline') != baseline:
            raise ValueError('partial update cannot change chart/configuration; perform a complete deployment')
        images = {r['project']: r['image'] for r in records if 'image' in r}
        if not images:
            raise ValueError('no selected images for this deployment')
        for name in images:
            prior = old_meta.get('services', {}).get(name, {})
            old_commit = prior.get('commit')
            current = os.environ['CI_COMMIT_SHA']
            if old_commit and old_commit != current:
                valid = subprocess.run(['git', 'merge-base', '--is-ancestor', old_commit, current], cwd=root, capture_output=True)
                if valid.returncode:
                    raise ValueError(f'{name}: stale/nonlinear image update; explicit rollback or complete reconciliation required')
        overlay = overlay_images(settings['images'], images, previous, partial and bool(listing))
        services = dict(old_meta.get('services', {})) if partial else {}
        for name in images:
            services[name] = {'commit': os.environ['CI_COMMIT_SHA'], 'pipeline': os.environ['CI_PIPELINE_ID']}
        overlay['genericCiMetadata'] = {'baseline': baseline, 'services': services}
        # Metadata is Helm values state, not a separate desired-state repository.
        generated = Path(tmp) / 'images.json'
        write_json(generated, overlay)
        args = [release, str(chart_path), '--namespace', namespace]
        for path in value_files:
            args += ['--values', str(path)]
        args += ['--values', str(generated)]
        rendered = subprocess.check_output(['helm', 'template', *args], env=env, text=True)
        workload_images = set()
        def scan(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {'containers', 'initContainers'} and isinstance(child, list):
                        workload_images.update(v.get('image') for v in child if isinstance(v, dict))
                    scan(child)
            elif isinstance(value, list):
                for child in value:
                    scan(child)
        for document in yaml.safe_load_all(rendered):
            scan(document)
        for binding in settings['images']:
            name = binding['from_'].split('.')[0]
            if name not in images:
                continue
            image = images[name]
            expected = image['repository'] + ('@' + image['digest'] if binding['set']['digest'] else ':' + image['tag'])
            if expected not in workload_images:
                raise ValueError(f'chart does not consume mapped image for {name}: {expected}')
        major = subprocess.check_output(['helm', 'version', '--template', '{{.Version}}'], text=True)
        flag = '--atomic' if major.startswith('v3.') else '--rollback-on-failure' if major.startswith('v4.') else None
        if not flag:
            raise ValueError('supported Helm versions: 3 and 4')
        subprocess.run(['helm', 'upgrade', '--install', *args, '--wait', '--timeout', '10m', flag], env=env, check=True)
        write_json(out / 'images.json', images)
        return {'release': release, 'namespace': namespace, 'url': url, 'images': images, 'baseline': baseline}
