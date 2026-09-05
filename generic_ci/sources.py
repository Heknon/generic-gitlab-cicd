"""Pinned, data-only Git configuration sources; no checkout or source code execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import Field

from . import __version__
from .config import read_yaml, UniqueLoader
from .models import Model, relative
from .workflows.compiler import merge
from .workflows.models import Pipeline, Platform


class Source(Model):
    repository: str
    ref: str


class ProjectConfig(Model):
    source: Source
    delivery: str = 'delivery.yml'
    platform: str | None = None


class Manifest(Model):
    version: int = 1
    cli: str = '>=0.4.0,<0.5.0'
    defaults: dict[str, str] = Field(default_factory=dict)
    templates: dict[str, str] = Field(default_factory=dict)


def location(root, value):
    relative(value)
    if any(part.lower() == '.git' for part in Path(value).parts):
        raise ValueError('source files must not modify Git metadata')
    result = root / value
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError('path escapes its repository')
    return result


def home():
    return Path(os.environ.get('GENERIC_CI_HOME', Path.home() / '.config/generic-ci'))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def repository(value):
    # Credentials belong to Git's credential helper or SSH agent, never the lock.
    if value.startswith(('https://', 'ssh://')):
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.password or parsed.query or parsed.fragment or (parsed.scheme == 'https' and parsed.username):
            raise ValueError('repository must not contain credentials, query strings or fragments')
    elif re.fullmatch(r'[\w.-]+@[\w.-]+:[^\s]+', value):
        pass
    elif '://' in value or value.startswith('-'):
        raise ValueError('use HTTPS, SSH, or an existing local Git repository/bundle')
    else:
        path = Path(value).expanduser().resolve()
        value = str(path)
    return value


def git(cache, *args, binary=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(cache), *args], env=env,
                            capture_output=True, text=not binary)
    if result.returncode:
        # Git stderr may contain credential-helper output or authenticated URLs.
        raise ValueError('source Git operation failed; check repository access and revision availability')
    return result.stdout if binary else result.stdout.strip()


def cache_for(repo):
    return home() / 'cache' / hashlib.sha256(repo.encode()).hexdigest()


def resolve(source, *, commit=None, offline=False, import_from=None):
    repo = repository(source.repository)
    if not source.ref or source.ref.startswith('-') or any(c.isspace() for c in source.ref):
        raise ValueError('source ref must be a Git branch, tag or commit')
    cache = cache_for(repo)
    if commit and not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('source lock must contain a full Git commit')
    if commit and cache.exists():
        try:
            if git(cache, 'rev-parse', '--verify', commit + '^{commit}') == commit:
                return cache, commit
        except ValueError:
            pass
    if offline and not import_from:
        raise ValueError('source revision is not cached; import a checkout/bundle or fetch it while connected')
    if not cache.exists():
        cache.mkdir(parents=True)
        git(cache, 'init', '--bare')
    origin = repository(import_from) if import_from else repo
    revision = commit or source.ref
    git(cache, 'fetch', '--no-tags', '--', origin, revision)
    resolved = git(cache, 'rev-parse', 'FETCH_HEAD^{commit}')
    if commit and resolved != commit:
        raise ValueError('import does not match the locked source commit')
    return cache, resolved


def blob(cache, commit, path):
    relative(path)
    mode = git(cache, 'ls-tree', commit, '--', path).split()
    if not mode or mode[0] not in {'100644', '100755'}:
        raise ValueError(f'source path must be a regular tracked file: {path}')
    data = git(cache, 'show', commit + ':' + path, binary=True)
    if len(data) > 2_000_000:
        raise ValueError('source file exceeds 2 MB')
    return data


def yaml_blob(cache, commit, path):
    data = yaml.load(blob(cache, commit, path).decode(), Loader=UniqueLoader)
    if not isinstance(data, dict):
        raise ValueError(f'{path}: expected YAML mapping')
    return data


def snapshot(source, cache, commit):
    manifest = Manifest.model_validate(yaml_blob(cache, commit, 'generic-ci-source.yml'))
    if manifest.version != 1 or Version(__version__) not in SpecifierSet(manifest.cli):
        raise ValueError('source manifest version or CLI compatibility requirement is not satisfied')
    if set(manifest.defaults) - {'delivery', 'platform'}:
        raise ValueError('source defaults supports only delivery and platform')
    defaults = {key: yaml_blob(cache, commit, path) for key, path in manifest.defaults.items()}
    return {'version': 1, 'source': source.model_dump(), 'commit': commit,
            'manifest': manifest.model_dump(), 'defaults': defaults}


def lock_path(root):
    return root / 'generic-ci.lock.json'


def project_config(root):
    return ProjectConfig.model_validate(read_yaml(root / 'generic-ci.yml'))


def locked(root, *, offline=False, import_from=None):
    config = project_config(root)
    path = lock_path(root)
    if not path.exists():
        raise ValueError('source lock is missing; run generic-ci source update')
    lock = json.loads(path.read_text())
    if lock.get('source') != config.source.model_dump() or lock.get('version') != 1:
        raise ValueError('source configuration changed; run generic-ci source update')
    if not re.fullmatch('[a-f0-9]{40}', str(lock.get('commit', ''))):
        raise ValueError('source lock must contain a full Git commit')
    cache, commit = resolve(config.source, commit=lock['commit'], offline=offline, import_from=import_from)
    actual = snapshot(config.source, cache, commit)
    if lock != actual:
        raise ValueError('source lock content differs from its pinned Git revision')
    return config, actual, cache


def apply_layers(layers):
    effective, origins = {}, {}
    def mark(value, prefix, owner):
        if isinstance(value, dict) and value:
            for key, child in value.items():
                mark(child, prefix + ('.' if prefix else '') + key, owner)
        else:
            origins[prefix] = owner
    for owner, data in layers:
        if not isinstance(data, dict):
            raise ValueError('configuration layers must be mappings')
        effective = merge(effective, data)
        mark(data, '', owner)
    # Drop origins for leaves replaced by a scalar/list or vice versa.
    valid = {}
    def retain(value, prefix=''):
        if isinstance(value, dict) and value:
            for key, child in value.items():
                retain(child, prefix + ('.' if prefix else '') + key)
        elif prefix in origins:
            valid[prefix] = origins[prefix]
    retain(effective)
    return effective, valid


def load_project(root, config_override=None, platform_override=None, offline=False):
    config, lock, _ = locked(root, offline=offline)
    delivery = location(root, config_override or config.delivery)
    platform_name = platform_override or config.platform
    delivery_data, dorigins = apply_layers([('source:' + lock['commit'], lock['defaults'].get('delivery', {})),
                                          ('project:' + delivery.relative_to(root).as_posix(), read_yaml(delivery))])
    platform_layers = [('source:' + lock['commit'], lock['defaults'].get('platform', {}))]
    paths = [root / 'generic-ci.yml', lock_path(root), delivery]
    if platform_name:
        local = location(root, platform_name)
        platform_layers.append(('project:' + platform_name, read_yaml(local)))
        paths.append(local)
    platform_data, porigins = apply_layers(platform_layers)
    return Pipeline.model_validate(delivery_data), Platform.model_validate(platform_data), {'delivery': dorigins, 'platform': porigins}, paths


def initialize(root, template, name=None, repo=None, ref=None, offline=False, delivery=None):
    if repo:
        if not ref:
            raise ValueError('--repo requires --ref')
        source = Source(repository=repository(repo), ref=ref)
    else:
        registry = json.loads((home() / 'sources.json').read_text())
        chosen = name or registry.get('default')
        if chosen not in registry.get('sources', {}):
            raise ValueError('choose a configured source with --source or provide --repo and --ref')
        source = Source.model_validate(registry['sources'][chosen])
    # Registry records the initial pinned revision for offline initialization.
    registry_path = home() / 'pins.json'
    pins = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    pin = pins.get(json.dumps(source.model_dump(), sort_keys=True))
    cache, commit = resolve(source, commit=pin, offline=offline)
    snap = snapshot(source, cache, commit)
    templates = snap['manifest']['templates']
    if not template:
        print('Available templates: ' + ', '.join(sorted(templates)))
        return
    if template not in templates:
        raise ValueError('unknown template; available: ' + ', '.join(sorted(templates)))
    prefix = templates[template].rstrip('/')
    relative(prefix)
    paths = git(cache, 'ls-tree', '-r', '--name-only', commit, '--', prefix).splitlines()
    files = {}
    for path in paths:
        if not path.startswith(prefix + '/'):
            continue
        destination = location(root, path[len(prefix) + 1:])
        files[destination] = blob(cache, commit, path)
    if not files:
        raise ValueError('template contains no files')
    project = ProjectConfig(source=source, delivery=delivery or 'delivery.yml')
    for reserved in [root / 'generic-ci.yml', lock_path(root)]:
        if reserved in files:
            raise ValueError('template cannot supply reserved source/lock files')
    files[root / 'generic-ci.yml'] = yaml.safe_dump(project.model_dump(by_alias=True, exclude_none=True), sort_keys=False).encode()
    files[lock_path(root)] = (json.dumps(snap, indent=2, sort_keys=True) + '\n').encode()
    if location(root, project.delivery) not in files:
        raise ValueError('template does not contain the configured delivery file')
    for path in files:
        if path.exists() or path.is_symlink():
            raise ValueError(f'init will not overwrite {path.relative_to(root)}')
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    print(f'Created {template} from source commit {commit}; commit generic-ci.yml and generic-ci.lock.json')


def source_main(argv):
    parser = argparse.ArgumentParser(prog='generic-ci source')
    sub = parser.add_subparsers(dest='action', required=True)
    add = sub.add_parser('add'); add.add_argument('name'); add.add_argument('--repo', required=True); add.add_argument('--ref', required=True); add.add_argument('--default', action='store_true')
    sub.add_parser('list')
    update = sub.add_parser('update'); update.add_argument('--ref'); update.add_argument('--check', action='store_true')
    fetch = sub.add_parser('fetch'); fetch.add_argument('--from', dest='import_from'); fetch.add_argument('--offline', action='store_true')
    for p in [update, fetch]: p.add_argument('--root', default='.')
    args = parser.parse_args(argv)
    registry_path = home() / 'sources.json'
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {'sources': {}}
    if args.action == 'list':
        print(json.dumps(registry, indent=2)); return 0
    if args.action == 'add':
        source = Source(repository=repository(args.repo), ref=args.ref)
        cache, commit = resolve(source); snapshot(source, cache, commit)
        registry['sources'][args.name] = source.model_dump()
        if args.default: registry['default'] = args.name
        write_json(registry_path, registry)
        pins_path = home() / 'pins.json'
        pins = json.loads(pins_path.read_text()) if pins_path.exists() else {}
        pins[json.dumps(source.model_dump(), sort_keys=True)] = commit
        write_json(pins_path, pins)
        print(f'Registered {args.name} at {commit}'); return 0
    root = Path(args.root).resolve()
    if args.action == 'fetch':
        _, snap, _ = locked(root, offline=args.offline, import_from=args.import_from)
        print('Cached ' + snap['commit']); return 0
    config = project_config(root)
    if args.ref: config.source.ref = args.ref
    cache, commit = resolve(config.source)
    updated = snapshot(config.source, cache, commit)
    old = json.loads(lock_path(root).read_text()) if lock_path(root).exists() else {}
    import difflib
    before = json.dumps(old.get('defaults', {}), indent=2, sort_keys=True).splitlines(True)
    after = json.dumps(updated['defaults'], indent=2, sort_keys=True).splitlines(True)
    print('Source revision: ' + old.get('commit', '(none)') + ' -> ' + commit)
    print(''.join(difflib.unified_diff(before, after, fromfile='old defaults', tofile='new defaults')), end='')
    # Check the complete effective configuration before recording the update.
    delivery = read_yaml(location(root, config.delivery))
    effective = Pipeline.model_validate(merge(updated['defaults'].get('delivery', {}), delivery))
    platform = updated['defaults'].get('platform', {})
    if config.platform: platform = merge(platform, read_yaml(location(root, config.platform)))
    from .workflows.compiler import compile_pipeline
    compile_pipeline(effective, Platform.model_validate(platform))
    if args.check: return 0 if old == updated else 1
    write_json(lock_path(root), updated)
    (root / 'generic-ci.yml').write_text(yaml.safe_dump(config.model_dump(by_alias=True, exclude_none=True), sort_keys=False))
    print('Updated source lock; review the diff and regenerate CI. Template files were preserved.')
    return 0
