"""Compile event workflows into explicit jobs and verifiable artifact dependencies."""
import base64
import copy
import json
import re
import yaml
from generic_ci import __version__
from generic_ci.compiler import digest, variants
from generic_ci.config import read_yaml, allowed_url
from .models import Pipeline, Platform


def merge(*values):
    result = {}
    for value in values:
        for key, item in value.items():
            result[key] = merge(result.get(key, {}), item) if isinstance(item, dict) and isinstance(result.get(key, {}), dict) else copy.deepcopy(item)
    return result


def load(config, platform):
    return Pipeline.model_validate(read_yaml(config)), Platform.model_validate(read_yaml(platform))


def tag_pattern(tag):
    return '^' + re.escape(tag).replace(re.escape('{version}'), '.+') .replace('/', r'\/') + '$'


def event_rule(event, project=None):
    if event == 'release':
        return '$CI_COMMIT_TAG =~ /' + tag_pattern(project['release']['tag']) + '/'
    return {'push': '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH',
            'merge-request': '$CI_PIPELINE_SOURCE == "merge_request_event"',
            'manual': '$CI_PIPELINE_SOURCE == "web" && $CI_COMMIT_BRANCH',
            'schedule': '$CI_PIPELINE_SOURCE == "schedule" && $CI_COMMIT_BRANCH'}[event]


def compile_pipeline(pipeline, platform, sources=None):
    p, infra = pipeline.model_dump(), platform.model_dump()
    projects, nodes, refs = p['projects'], {}, {}
    # Defaults on models describe the schema, not explicit override requests.
    for name, project in projects.items():
        source = pipeline.projects[name]
        project['defaults'] = source.defaults.model_dump(exclude_unset=True)
        for key, settings in project['checks'].items():
            explicit = source.checks[key].model_dump(exclude_unset=True)
            for field in ('image', 'tags', 'timeout', 'variables', 'services', 'parallel', 'artifacts'):
                if field not in explicit:
                    settings.pop(field, None)
            if isinstance(settings['dependencies'], dict):
                settings['dependencies'] = source.checks[key].dependencies.model_dump(exclude_unset=True)
    hosts = infra['allowed_hosts']
    for value in [*infra['images'].values(), infra['container_builder']['image'], *infra['registries'].values()]:
        allowed_url('https://' + value.split('/')[0], hosts)
    if infra['registries']['containers'] == infra['registries']['previews']:
        raise ValueError('release and preview registries must be distinct repositories')
    for name, project in projects.items():
        for dep in project['depends_on']:
            if dep not in projects or dep == name:
                raise ValueError(f'{name}.depends-on: unknown/self project {dep}')
        if project['container'] and project['container']['repository']:
            allowed_url('https://' + project['container']['repository'].split('/')[0], hosts)
    def project_visit(name, stack):
        if name in stack:
            raise ValueError('project dependency cycle: ' + ' -> '.join([*stack, name]))
        for dep in projects[name]['depends_on']:
            project_visit(dep, [*stack, name])
    for name in projects:
        project_visit(name, [])

    def add(key, project, event, action, settings, needs=(), execution=None, condition=None, deployment=None):
        candidate = dict(project=project, event=event, action=action, settings=settings,
                          needs=list(needs), execution=execution or {}, condition=condition,
                          deployment=deployment)
        if key in nodes and nodes[key] != candidate:
            raise ValueError(f'generated job name collision: {key}; rename the user check or project')
        nodes[key] = candidate
        return key

    def check(name, key, event, context=None):
        project = projects[name]
        if key not in project['checks']:
            raise ValueError(f'{name}: unknown check {key}; available: {list(project["checks"])}')
        settings = project['checks'][key]
        execution = merge(infra['defaults'], project['defaults'], {k: v for k, v in settings.items() if v is not None})
        if settings['dependencies'] is not None and settings['dependencies'] is not False and project['python'] is None:
            raise ValueError(f'{name}.{key}: dependency group overrides require Python')
        rows = variants(execution)
        if settings['outputs'] and len(rows) != 1:
            raise ValueError(f'{name}.{key}: matrix output paths would collide')
        result = []
        for index, row in enumerate(rows):
            identifier = ':'.join(filter(None, [event, context, name, key, str(index + 1) if len(rows) > 1 else None]))
            ex = merge(execution, {'variables': row})
            result.append(add(identifier, name, event, 'check', settings, execution=ex, deployment=context))
        if not context:
            refs[event, name + '.' + key] = result
        return result

    for name, project in projects.items():
        for event, workflow in project['workflows'].items():
            if event == 'release' and not project['release']:
                raise ValueError(f'{name}: release workflow requires release metadata')
            gates = [job for key in workflow['checks'] for job in check(name, key, event)]
            if project['release']:
                vjob = add(f'{event}:{name}:version', name, event, 'version', project['release'])
                gates.append(vjob)
            operations = workflow['build']
            available = {'application': project['build'], 'container': project['container'], 'package': project['package']}
            if operations is True:
                operations = [kind for kind, setting in available.items() if setting is not None]
                if len(operations) != 1:
                    raise ValueError(f'{name}: build: true requires exactly one output; list application/container/package explicitly')
            elif operations is False:
                operations = []
            publication = workflow['publish']
            channel = publication['channel'] if isinstance(publication, dict) else 'auto'
            if publication:
                if channel == 'development':
                    if event == 'release':
                        raise ValueError(f'{name}: development publication cannot run on release tags')
                elif event != 'release':
                    raise ValueError(f'{name}: auto publication requires a release workflow')
                if 'package' not in operations:
                    operations = [*operations, 'package']
            built = []
            for kind in operations:
                if available[kind] is None:
                    raise ValueError(f'{name}: no {kind} build configured')
                public = {'application': 'build', 'container': 'build-image', 'package': 'build-package'}[kind]
                key = add(f'{event}:{name}:{public}', name, event, kind, available[kind], gates)
                if kind == 'package':
                    nodes[key]['settings'] = {**nodes[key]['settings'], 'channel': channel if publication else 'auto'}
                nodes[key]['execution'] = merge(infra['defaults'], project['defaults'])
                refs[event, name + '.' + public] = [key]
                built.append(key)
            if event == 'release' or publication:
                final = add(f'{event}:{name}:publish', name, event, 'publish', {'publish_package': bool(publication), 'channel': channel}, gates + built)
                refs[event, name + '.publish'] = [final]
        release = project['release']
        if release and release['require_bump'] and 'merge-request' not in project['workflows']:
            add(f'merge-request:{name}:version', name, 'merge-request', 'version', release)
        if release and release['create']:
            if 'push' not in project['workflows'] or 'release' not in project['workflows']:
                raise ValueError(f'{name}: release button requires push and release workflows')
            gates = [k for k, n in nodes.items() if n['project'] == name and n['event'] == 'push']
            add(f'push:{name}:create-release', name, 'push', 'create-release', release, gates,
                condition='$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == ' + json.dumps(release['create']['branch']))

    # Explicit artifact edges never substitute packages. Producers must be selected in that event.
    for key, node in list(nodes.items()):
        if node['action'] not in {'check', 'application', 'container', 'package', 'publish'}:
            continue
        requested = node['settings'].get('needs', [])
        if node['action'] == 'publish' and node['event'] == 'release':
            requested = [dep + '.publish' for dep in projects[node['project']]['release']['needs']]
        for ref in requested:
            ref = ref if '.' in ref else node['project'] + '.' + ref
            if (node['event'], ref) not in refs:
                raise ValueError(f'{key}: producer {ref} is not selected in {node["event"]}')
            node['needs'] += refs[node['event'], ref]
            if node['event'] == 'release':
                dep = ref.split('.')[0]
                if projects[dep]['release']['tag'] != projects[node['project']]['release']['tag']:
                    raise ValueError('release artifact/publication dependencies require a coordinated shared release tag')

    for name, deployment in p['deployments'].items():
        if deployment['target'] not in infra['targets']:
            raise ValueError(f'{name}: unknown target {deployment["target"]}')
        chart = deployment['chart']
        for source in ('repository', 'oci'):
            if chart[source]:
                allowed_url(chart[source], hosts)
        paths = [v for binding in deployment['images'] for v in binding['set'].values() if v]
        if any(a == b or a.startswith(b + '.') or b.startswith(a + '.') for i, a in enumerate(paths) for b in paths[i+1:]):
            raise ValueError(f'{name}: overlapping image mappings')
        for event, behavior in deployment['workflows'].items():
            if event == 'release' and deployment['update'] == 'complete':
                tags = {projects[b['from_'].split('.')[0]]['release']['tag']
                        for b in deployment['images']
                        if b['from_'].split('.')[0] in projects and projects[b['from_'].split('.')[0]]['release']}
                if len(tags) > 1:
                    raise ValueError(f'{name}: complete release deployment requires a coordinated shared release tag; use partial for independent tags')
            producers = []
            conditions = []
            for binding in deployment['images']:
                ref = binding['from_']
                if (event, ref) not in refs or not ref.endswith('.build-image'):
                    raise ValueError(f'{name}: image producer {ref} must build in {event}')
                producers += refs[event, ref]
                conditions.append(event_rule(event, projects[ref.split('.')[0]]))
            condition = ' || '.join('(' + c + ')' for c in dict.fromkeys(conditions))
            if event == 'merge-request':
                condition = '(' + condition + ') && $CI_MERGE_REQUEST_SOURCE_PROJECT_ID == $CI_PROJECT_ID'
            key = f'{event}:deploy:{name}'
            prior = producers[:]
            if behavior['when'] == 'manual':
                approval = add(f'{event}:approve:{name}', None, event, 'approve', deployment, producers, condition=condition, deployment=name)
                prior.append(approval)
            before_dependencies = prior[:]
            for ref in deployment['before']['checks']:
                owner, ck = ref.split('.', 1)
                if owner not in projects:
                    raise ValueError(f'unknown check owner {owner}')
                for job in check(owner, ck, event, name + '-before'):
                    nodes[job]['needs'] = before_dependencies[:]
                    nodes[job]['condition'] = condition
                    nodes[job]['deployment'] = name
                    prior.append(job)
            add(key, None, event, 'deploy', deployment, prior, condition=condition, deployment=name)
            for ref in deployment['after']['checks']:
                owner, ck = ref.split('.', 1)
                if owner not in projects:
                    raise ValueError(f'unknown check owner {owner}')
                for job in check(owner, ck, event, name + '-after'):
                    nodes[job]['needs'] = [key]
                    nodes[job]['condition'] = condition
                    nodes[job]['deployment'] = name
            if event == 'merge-request':
                add(f'{event}:stop:{name}', None, event, 'stop', deployment, condition=condition, deployment=name)

    # Contextual deployment checks retain the same explicit artifact contract.
    for key, node in nodes.items():
        if node['action'] == 'check' and node['deployment']:
            for ref in node['settings'].get('needs', []):
                ref = ref if '.' in ref else node['project'] + '.' + ref
                if (node['event'], ref) not in refs:
                    raise ValueError(f'{key}: producer {ref} is not selected in {node["event"]}')
                node['needs'] += refs[node['event'], ref]

    visiting, visited = set(), set()
    def visit(key):
        if key in visiting:
            raise ValueError(f'job dependency cycle: {key}')
        if key in visited:
            return
        visiting.add(key)
        for dep in nodes[key]['needs']:
            visit(dep)
        visiting.remove(key)
        visited.add(key)
    for key in nodes:
        visit(key)
    if len(nodes) + 1 > infra['max_jobs']:
        raise ValueError('generated job count exceeds platform max-jobs')
    payload = {'version': __version__, 'format': 'workflows-v1', 'pipeline': p, 'platform': infra, 'nodes': nodes, 'sources': sources or {}}
    fingerprint = digest(payload)
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    if len(encoded) > 100000:
        raise ValueError('execution configuration exceeds 100 KB; split project groups')
    control = infra['images'].get('control', infra['images'].get('python'))
    if not control:
        raise ValueError('platform images.control or images.python is required for the planner')
    output = {'stages': ['prepare', 'delivery'], 'workflow': {'rules': [
        {'if': '$CI_PIPELINE_SOURCE == "merge_request_event"'},
        {'if': '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_TAG'},
        {'if': '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS', 'when': 'never'},
        {'if': '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH'},
        {'if': '$CI_PIPELINE_SOURCE == "web" && $CI_COMMIT_BRANCH'},
        {'if': '$CI_PIPELINE_SOURCE == "schedule" && $CI_COMMIT_BRANCH'}]},
        'variables': {**infra['variables'], 'TOOLKIT_CONFIG_B64': encoded},
        'toolkit-plan': {'stage': 'prepare', 'image': control, 'tags': infra['defaults']['tags'] or [],
                         'script': [f'python -m generic_ci.workflows.runtime plan {fingerprint}'],
                         'artifacts': {'paths': ['.ci-out/plan.json']}}}
    for key, node in nodes.items():
        project = projects.get(node['project'], {})
        role = 'bun' if (project.get('node') or {}).get('package_manager') == 'bun' else 'node' if project.get('node') else 'python'
        if node['action'] in {'deploy', 'stop'}:
            role = 'helm'
        if node['action'] in {'create-release', 'version', 'publish', 'approve'}:
            role = 'control' if node['action'] != 'publish' else role
        ex = node['execution']
        if not ex.get('image') and role not in infra['images'] and role != 'control' and node['action'] != 'container':
            raise ValueError(f'platform images.{role} is required for {key}')
        image = ex.get('image') or infra['images'].get(role, control)
        tags = ex.get('tags') if ex.get('tags') is not None else infra['defaults']['tags'] or []
        if node['action'] == 'container':
            image = infra['container_builder']['image']
            tags = infra['container_builder']['tags'] or tags
        allowed_url('https://' + image.split('/')[0], hosts)
        for service in ex.get('services') or []:
            allowed_url('https://' + service.split('/')[0], hosts)
        if len(node['needs']) >= 50:
            raise ValueError(f'{key}: exceeds supported needs limit')
        condition = node['condition'] or event_rule(node['event'], project)
        rule = {'if': condition}
        if node['action'] in {'create-release', 'stop', 'approve'}:
            rule.update(when='manual', allow_failure=node['action'] == 'stop')
        job = {'stage': 'delivery', 'image': image, 'tags': tags, 'rules': [rule],
               'script': [f'python -m generic_ci.workflows.runtime run {key} {fingerprint}'],
               'needs': [{'job': 'toolkit-plan', 'artifacts': True}] + [{'job': d, 'artifacts': True, 'optional': True} for d in sorted(set(node['needs']))],
               'artifacts': merge(ex.get('artifacts', {}), {'when': 'always'}), 'variables': ex.get('variables', {}), 'retry': 0}
        job['artifacts']['paths'] = list(dict.fromkeys(job['artifacts'].get('paths', []) + [f'.ci-out/{key}/']))
        for field in ('timeout', 'services'):
            if ex.get(field) is not None:
                job[field] = ex[field]
        if node['action'] in {'deploy', 'stop'}:
            target = infra['targets'][node['settings']['target']]
            release = target['release'] or node['deployment']
            suffix = '/mr-$CI_MERGE_REQUEST_IID' if node['event'] == 'merge-request' else ''
            env = node['settings']['target'] + '/' + release + suffix
            job['resource_group'] = env
            job['environment'] = {'name': env, 'url': target['url']}
            if node['event'] == 'merge-request':
                job['environment'].update(on_stop=f'merge-request:stop:{node["deployment"]}', auto_stop_in=node['settings']['auto_stop_in'])
            if node['action'] == 'stop':
                job['environment'] = {'name': env, 'action': 'stop'}
                job['needs'] = []
                job['variables'] = {'GIT_STRATEGY': 'none'}
        if node['action'] in {'create-release', 'publish'}:
            job['resource_group'] = 'release/' + node['project']
        output[key] = job
    for key in ['toolkit-plan', *nodes]:
        if not output[key].get('tags'):
            output[key].pop('tags', None)
    return output, payload
