"""Package version generation and user-selected publication destinations."""
import json
import os
import re
import tomllib
from urllib.parse import urlsplit, urlunsplit
from packaging.version import Version
import tomlkit
from generic_ci.config import allowed_url
from .ecosystems import version_key


def snapshot_version(value, node=False):
    pipeline = os.environ.get('CI_PIPELINE_ID', '')
    if not re.fullmatch(r'[1-9][0-9]*', pipeline):
        raise ValueError('development versions require a numeric CI_PIPELINE_ID')
    if node:
        version_key(value, True)
        return value.split('-')[0].split('+')[0] + '-dev.' + pipeline
    version = Version(value)
    if version.local or version.post is not None:
        raise ValueError('development builds require a version without local/post suffixes')
    return version.base_version + '.dev' + pipeline


def release_channel(value, node=False):
    if node:
        version_key(value, True)
        pre = value.split('+')[0].partition('-')[2]
        label = pre.split('.')[0] if pre else 'latest'
        return 'next' if label.isdigit() else label
    version = Version(value)
    if version.is_devrelease:
        return 'dev'
    return {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[version.pre[0]] if version.pre else 'latest'


def endpoint(value, hosts):
    allowed_url(value, hosts)
    parsed = urlsplit(value)
    return urlunsplit(('https', parsed.netloc.lower().removesuffix(':443'), parsed.path.rstrip('/'), '', ''))


def destination(project, directory, hosts, development=False):
    settings = project['package']
    preview = settings.get('preview') or {}
    if project['python'] is not None:
        rows = tomllib.loads((directory / 'pyproject.toml').read_text()).get('tool', {}).get('uv', {}).get('index', [])
        name = (preview.get('index') or settings['index']) if development else settings['index']
        selected = [row for row in rows if row.get('name') == name and row.get('publish-url')]
        if len(selected) != 1:
            raise ValueError('select one named uv index with publish-url in pyproject.toml')
        row = selected[0]
        url = endpoint(row['publish-url'], hosts)
        return {'index': name, 'url': url, 'check_url': endpoint(row['url'], hosts)}
    meta = json.loads((directory / 'package.json').read_text())
    normal = preview.get('registry') if development else None
    normal = normal or meta.get('publishConfig', {}).get('registry')
    if not normal:
        raise ValueError('package.json publishConfig.registry is required')
    normal = endpoint(normal, hosts)
    return {'url': normal}


def rewrite_snapshot(directory, node, target):
    path = directory / ('package.json' if node else 'pyproject.toml')
    meta = json.loads(path.read_text()) if node else tomlkit.parse(path.read_text())
    package = meta if node else meta['project']
    original = package.get('version')
    if not isinstance(original, str):
        raise ValueError('development publication requires a static manifest version')
    package['version'] = snapshot_version(original, node)
    if node:
        # npm can prefer the archive's publishConfig over command-line options.
        meta['publishConfig'] = {**meta.get('publishConfig', {}), 'registry': target['url'], 'tag': 'dev'}
    path.write_text(json.dumps(meta, indent=2) + '\n' if node else tomlkit.dumps(meta))
    return package['version']


def check_development_context(plan):
    if plan['event'] == 'release' or os.environ.get('CI_COMMIT_TAG'):
        raise ValueError('development publication generates a snapshot; use auto to publish the tagged version')
