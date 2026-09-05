"""Package publication policy: isolated snapshots and deliberate tagged releases."""
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
        if label not in {'latest', 'alpha', 'beta', 'rc'} or (pre and label == 'latest'):
            raise ValueError('release packages support alpha, beta, rc or stable; dev versions belong in previews')
        return label
    version = Version(value)
    if version.is_devrelease or version.local:
        raise ValueError('development versions belong in previews, not release publication')
    return {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[version.pre[0]] if version.pre else 'latest'


def endpoint(value, hosts):
    allowed_url(value, hosts)
    parsed = urlsplit(value)
    if parsed.fragment or '..' in parsed.path.split('/') or '%' in parsed.path:
        raise ValueError('package endpoint must be a canonical HTTPS URL')
    return urlunsplit(('https', parsed.netloc.lower().removesuffix(':443'), parsed.path.rstrip('/'), '', ''))


def destination(project, directory, hosts, development=False):
    settings = project['package']
    preview = settings.get('preview') or {}
    if project['python'] is not None:
        rows = tomllib.loads((directory / 'pyproject.toml').read_text()).get('tool', {}).get('uv', {}).get('index', [])
        name = preview.get('index') if development else settings['index']
        selected = [row for row in rows if row.get('name') == name and row.get('publish-url')]
        if len(selected) != 1:
            raise ValueError('select one named uv index with publish-url in pyproject.toml')
        row = selected[0]
        url = endpoint(row['publish-url'], hosts)
        if development:
            if row.get('explicit') is not True or name == settings['index']:
                raise ValueError('preview index must be explicit=true and distinct from the release index')
            read_url = endpoint(row['url'], hosts)
            for other in rows:
                if other is row:
                    continue
                if any(endpoint(other[key], hosts) in {url, read_url} for key in ('url', 'publish-url') if other.get(key)):
                    raise ValueError('preview endpoints must not also be configured as normal indexes')
        return {'index': name, 'url': url, 'check_url': endpoint(row['url'], hosts)}
    meta = json.loads((directory / 'package.json').read_text())
    if meta.get('private'):
        raise ValueError('cannot publish private package')
    normal = meta.get('publishConfig', {}).get('registry')
    if not normal:
        raise ValueError('package.json publishConfig.registry is required')
    normal = endpoint(normal, hosts)
    url = endpoint(preview.get('registry', ''), hosts) if development else normal
    if development and url == normal:
        raise ValueError('preview registry must differ from publishConfig.registry')
    return {'url': url}


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
    if plan['candidates']:
        raise ValueError('package publication cannot contain candidate dependency overrides')
    if plan['event'] == 'release' or os.environ.get('CI_COMMIT_TAG'):
        raise ValueError('development publication cannot run on release tags')
    if plan['event'] == 'merge-request':
        project = os.environ.get('CI_PROJECT_ID')
        if not project or os.environ.get('CI_MERGE_REQUEST_SOURCE_PROJECT_ID') != project or os.environ.get('CI_MERGE_REQUEST_TARGET_PROJECT_ID') != project:
            raise ValueError('development publication requires a same-project merge request; forks cannot publish')
