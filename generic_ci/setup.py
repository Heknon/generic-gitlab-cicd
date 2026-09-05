"""Guided configuration authoring; never executes application commands."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

import yaml

from .models import relative


def setup_main(argv):
    parser = argparse.ArgumentParser(prog='generic-ci setup', description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--mode', choices=['organization', 'standalone'])
    parser.add_argument('--yes', action='store_true', help='Use supplied/default answers and write without prompting')
    parser.add_argument('--dry-run', action='store_true', help='Validate and preview without writing project files')
    parser.add_argument('--offline', action='store_true')
    for flag in ['repo', 'ref', 'source', 'template', 'app', 'path', 'ecosystem', 'test-command',
                 'runtime-image', 'builder-image', 'registry', 'preview-registry', 'runner-tag',
                 'deploy', 'helm-image', 'chart-oci', 'chart-version', 'namespace', 'hostname', 'port', 'tls']:
        parser.add_argument('--' + flag)
    args = parser.parse_args(argv)
    root = Path(args.root).absolute()
    if not root.is_dir():
        raise ValueError('--root must be an existing application directory')
    interactive = not args.yes and sys.stdin.isatty()
    if not interactive and not args.yes:
        raise ValueError('non-interactive setup requires --yes (use --dry-run to preview)')

    def ask(key, question, default=None, choices=None):
        value = getattr(args, key.replace('-', '_'))
        if value is None:
            if interactive:
                value = input(question + (f' [{default}]' if default is not None else '') + ': ').strip() or default
            else:
                value = default
        if value is None or value == '':
            raise ValueError(f'--{key} is required')
        if choices and value not in choices:
            raise ValueError(f'--{key} must be one of: {", ".join(choices)}')
        return value

    mode = ask('mode', 'Setup path: organization or standalone', 'organization' if args.repo or args.source else 'standalone', ['organization', 'standalone'])
    with tempfile.TemporaryDirectory(prefix='generic-ci-setup-') as temporary:
        stage = Path(temporary)
        if mode == 'organization':
            from .sources import initialize
            repo = None if args.source else ask('repo', 'Organization configuration Git repository (GitHub or GitLab URL)')
            ref = ask('ref', 'Source revision') if repo else None
            if not args.template:
                initialize(stage, None, args.source, repo, ref, args.offline)
            template = ask('template', 'Template name')
            initialize(stage, template, args.source, repo, ref, args.offline)
            descriptor = yaml.safe_load((stage / 'generic-ci.yml').read_text())
            delivery_path = stage / descriptor['delivery']
            config = yaml.safe_load(delivery_path.read_text())
            projects = config.get('projects', {})
            if projects:
                selected = ask('app', 'Application in template to configure', next(iter(projects)), list(projects))
                project = projects[selected]
                project['path'] = relative(ask('path', 'Application directory', project.get('path', '.')))
                checks = project.get('checks', {})
                if len(checks) == 1:
                    check = next(iter(checks.values()))
                    existing = check.get('script', [])
                    if len(existing) == 1:
                        check['script'] = [ask('test-command', 'Confirm template check command', existing[0])]
                elif args.test_command:
                    raise ValueError('template has multiple/no checks; edit its delivery configuration to select commands')
                delivery_path.write_text(yaml.safe_dump(config, sort_keys=False))
            if any(getattr(args, key) is not None for key in ['ecosystem', 'deploy', 'runtime_image', 'builder_image', 'registry', 'preview_registry', 'runner_tag', 'helm_image', 'chart_oci', 'chart_version', 'namespace', 'hostname', 'port', 'tls']):
                raise ValueError('organization mode inherits infrastructure/deployment settings from its template; use standalone mode for infrastructure flags')
        else:
            if args.repo or args.ref or args.source or args.template:
                raise ValueError('source options require organization mode')
            name = ask('app', 'Application name', 'app')
            path = relative(ask('path', 'Application directory relative to repository', '.'))
            application = root / path
            if not application.is_dir() or not application.resolve().is_relative_to(root.resolve()):
                raise ValueError('application directory must exist inside the repository')
            detected = 'python' if (application / 'pyproject.toml').exists() else 'npm' if (application / 'package.json').exists() else 'generic'
            for lock, manager in [('pnpm-lock.yaml', 'pnpm'), ('bun.lock', 'bun')]:
                if (application / lock).exists():
                    detected = manager
            ecosystem = ask('ecosystem', 'Detected ecosystem (confirm or change)', detected, ['python', 'npm', 'pnpm', 'bun', 'generic'])
            command = None
            if ecosystem in ['npm', 'pnpm', 'bun'] and (application / 'package.json').exists():
                if 'test' in json.loads((application / 'package.json').read_text()).get('scripts', {}):
                    command = ecosystem + ' run test'
            test = ask('test-command', 'Test command (existing command; never executed by setup)', command)
            runtime = ask('runtime-image', 'Prepared runtime image containing the matching toolkit')
            tag = ask('runner-tag', 'Runner tag')
            project = {'path': path, 'checks': {'test': {'script': [test]}},
                       'workflows': {'push': {'checks': ['test']}, 'merge-request': {'checks': ['test']}}}
            role = 'python' if ecosystem in ['python', 'generic'] else 'bun' if ecosystem == 'bun' else 'node'
            if ecosystem == 'python':
                project['python'] = {}
            elif ecosystem != 'generic':
                project['node'] = {'package-manager': ecosystem}
            deploy = ask('deploy', 'Create an OpenShift MR preview deployment? yes/no', 'no', ['yes', 'no'])
            platform = {'version': 1, 'defaults': {'tags': [tag]}, 'images': {role: runtime},
                        'allowed-hosts': [urlsplit('https://' + runtime).hostname],
                        'variables': {'UV_PYTHON_DOWNLOADS': 'never'}}
            delivery = {'version': 1, 'projects': {name: project}}
            if deploy == 'yes' or any((args.builder_image, args.registry, args.preview_registry)):
                builder = ask('builder-image', 'Prepared Buildah image containing the matching toolkit')
                registry = ask('registry', 'Release container registry/repository prefix')
                previews = ask('preview-registry', 'Preview container registry/repository prefix')
                platform['container-builder'] = {'image': builder}
                platform['registries'] = {'containers': registry, 'previews': previews}
                platform['allowed-hosts'] = sorted({urlsplit('https://' + x).hostname for x in [runtime, builder, registry, previews]})
            if deploy == 'yes':
                if not (application / 'Dockerfile').is_file():
                    raise ValueError('deployment setup requires an existing Dockerfile in the application directory')
                helm = ask('helm-image', 'Prepared Helm image containing Git, Python and the toolkit')
                chart = ask('chart-oci', 'Published generic-app 2.x compatible OCI chart URL')
                version = ask('chart-version', 'Pinned chart version')
                namespace = ask('namespace', 'Existing preview namespace')
                hostname = ask('hostname', 'Route hostname (auto lets OpenShift assign a unique hostname)' )
                port = int(ask('port', 'Application listening port', '8080'))
                if not 1 <= port <= 65535:
                    raise ValueError('--port must be between 1 and 65535')
                tls = ask('tls', 'Route TLS mode', 'edge', ['edge', 'reencrypt', 'passthrough', 'none'])
                project['workflows']['merge-request']['build'] = ['container']
                project['container'] = {'dockerfile': 'Dockerfile', 'context': '.'}
                platform['images']['helm'] = helm
                platform['allowed-hosts'] = sorted(set(platform['allowed-hosts'] + [urlsplit('https://' + helm).hostname, urlsplit(chart).hostname or '']))
                platform['targets'] = {'preview': {'namespace': namespace, 'production': False, 'kubeconfig-variable': 'PREVIEW_KUBECONFIG'}}
                delivery['deployments'] = {name: {'target': 'preview', 'chart': {'oci': chart, 'version': version},
                    'values': ['deploy/values.yaml'], 'images': [{'from': name + '.build-image',
                    'set': {'repository': 'apps.' + name + '.image.repository', 'tag': 'apps.' + name + '.image.tag'}}],
                    'workflows': {'merge-request': {'when': 'manual'}}}}
                # Helm values are literal: CI variables in a hostname are not expanded.
                if '$' in hostname:
                    raise ValueError('Route hostname is literal; use auto for an OpenShift-generated hostname')
                route = {'enabled': True}
                if hostname != 'auto':
                    route['host'] = hostname
                if tls != 'none':
                    route['tls'] = {'termination': tls, 'insecureEdgeTerminationPolicy': 'Redirect'}
                values = {'apps': {name: {'enabled': True, 'replicas': 1, 'image': {'repository': previews + '/' + name, 'tag': 'setup-placeholder'},
                    'config': {}, 'secretRefs': [], 'service': {'enabled': True, 'port': 80, 'targetPort': port}, 'route': route}}}
                (stage / 'deploy').mkdir()
                (stage / 'deploy/values.yaml').write_text(yaml.safe_dump(values, sort_keys=False))
            for filename, value in [('delivery.yml', delivery), ('ci-platform.yml', platform)]:
                (stage / filename).write_text(yaml.safe_dump(value, sort_keys=False))
        from .cli import main
        schema_dir = stage / '.generic-ci'
        schema_dir.mkdir(exist_ok=True)
        for name, extra in [('delivery.schema.json', []), ('platform.schema.json', ['--platform-schema'])]:
            target = schema_dir / name
            if target.exists():
                raise ValueError('template supplies a reserved setup schema path: ' + name)
            if main(['schema', *extra, '-o', str(target)]):
                raise ValueError('schema generation failed')
        import os
        descriptor = yaml.safe_load((stage / 'generic-ci.yml').read_text()) if (stage / 'generic-ci.yml').exists() else {}
        for name, schema in [(descriptor.get('delivery', 'delivery.yml'), 'delivery.schema.json'),
                             (descriptor.get('platform') or 'ci-platform.yml', 'platform.schema.json')]:
            target = stage / name
            if target.is_file():
                reference = Path(os.path.relpath(schema_dir / schema, target.parent)).as_posix()
                target.write_text('# yaml-language-server: $schema=' + reference + '\n' + target.read_text())
        if main(['validate', '--root', str(stage), *(['--offline'] if args.offline else [])]):
            raise ValueError('setup configuration did not validate; no project files written')
        if main(['render', '--root', str(stage), '-o', str(stage / '.gitlab-ci.yml'), *(['--offline'] if args.offline else [])]):
            raise ValueError('setup rendering failed; no project files written')
        notes = stage / 'CI-SETUP.md'
        if not notes.exists():
            notes.write_text('# CI setup — revision one\n\nReview delivery.yml and the generated .gitlab-ci.yml before committing.\n\n'
                '- Provide matching toolkit runtime images, runner tags, registry authentication and CA trust.\n'
                '- Local schemas: .generic-ci/delivery.schema.json and .generic-ci/platform.schema.json. In PyCharm Settings > Languages & Frameworks > Schemas and DTDs > JSON Schema Mappings, associate each schema with its YAML file. YAML-language-server editors can use the generated schema comment.\n'
                '- Commit package-manager lockfiles and declare the dependencies needed by your checks.\n'
                '- For previews, provide PREVIEW_KUBECONFIG as a GitLab file variable, an existing namespace, image pull secrets and a published compatible chart.\n'
                '- Review Route host uniqueness, TLS certificates/backend trust, probes and application secrets in deploy/values.yaml when present.\n'
                '- Run generic-ci validate and generic-ci render -o .gitlab-ci.yml after editing.\n'
                '- Validate with your GitLab CI Lint and execute a test pipeline. Setup does not build, publish, provision or deploy anything.\n')
        files = sorted(p for p in stage.rglob('*') if p.is_file())
        for file in files:
            dest = root / file.relative_to(stage)
            if dest.exists() or dest.is_symlink() or any(p.is_symlink() for p in dest.parents):
                raise ValueError(f'setup will not overwrite or follow symlinks: {dest}')
            if any(p.exists() and not p.is_dir() for p in dest.parents):
                raise ValueError(f'parent is not a directory: {dest}')
        print('Proposed files:')
        for file in files:
            print('  ' + file.relative_to(stage).as_posix())
        print('\nDelivery configuration:\n' + (stage / descriptor.get('delivery', 'delivery.yml')).read_text())
        if args.dry_run:
            print('Dry run: no project files written.')
            return 0
        if interactive and input('Write these files? [y/N]: ').strip().lower() not in ['y', 'yes']:
            print('Cancelled: no project files written.')
            return 0
        created = []
        try:
            for file in files:
                dest = root / file.relative_to(stage)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open('xb') as stream:
                    created.append(dest)
                    stream.write(file.read_bytes())
        except OSError:
            for file in created:
                file.unlink(missing_ok=True)
            raise
        print('Setup complete. Review CI-SETUP.md, validate with GitLab CI Lint, and commit the configuration.')
        return 0
