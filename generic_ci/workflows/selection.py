"""One selection contract for runtime plans, local explanations and GitLab rules."""
import fnmatch
from functools import lru_cache
from pathlib import PurePosixPath

WORKSPACE_FILES = {'uv.lock', 'package-lock.json', 'pnpm-lock.yaml', 'bun.lock',
                   'pyproject.toml', 'package.json', 'pnpm-workspace.yaml'}


def matches_path(path, pattern):
    """Portable GitLab-style globs: * stays within a segment; ** crosses directories."""
    parts, glob = PurePosixPath(path).parts, PurePosixPath(pattern).parts

    @lru_cache(None)
    def match(i, j):
        if j == len(glob):
            return i == len(parts)
        if glob[j] == '**':
            return match(i, j + 1) or i < len(parts) and match(i + 1, j)
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], glob[j]) and match(i + 1, j + 1)
    return match(0, 0)


def project_paths(project):
    path = str(PurePosixPath(project['path']))
    return (['**/*'] if path == '.' else [path, path + '/**/*']) + project['watch']


def deployment_paths(deployment):
    chart = deployment['chart']['path']
    return deployment['values'] + ([str(PurePosixPath(chart)) + '/**/*'] if chart else [])


def closure(data, event, selected):
    selected = set(selected)
    projects, deployments = data['pipeline']['projects'], data['pipeline']['deployments']
    while True:
        more = selected | ({n for n, p in projects.items() if set(p['depends_on']) & selected} if event != 'release' else set())
        active = set()
        for name, deployment in deployments.items():
            owners = {i['from_'].split('.')[0] for i in deployment['images']}
            if event in deployment['workflows'] and owners & selected:
                active.add(name)
                if event == 'merge-request' or deployment['update'] == 'complete':
                    more.update(owners)
        for node in data['nodes'].values():
            enabled = node['deployment'] in active if node['deployment'] else node['project'] in selected
            if node['event'] != event or not enabled:
                continue
            for dep in node['needs']:
                producer = data['nodes'][dep]
                deployment = deployments.get(node['deployment'])
                if deployment and event == 'release' and deployment['update'] == 'partial' and producer['action'] in {'container', 'release'}:
                    continue
                if producer['project'] is not None:
                    more.add(producer['project'])
        if more == selected:
            return selected, active
        selected = more


def select(data, event, changed=None, candidates=(), tag=''):
    from .runtime import matches
    projects = data['pipeline']['projects']
    reasons = {n: [] for n in projects}
    if event == 'release':
        direct = {n for n, p in projects.items() if matches(tag, p['release'])}
        if not direct:
            raise ValueError('tag matches no project release')
        for name in direct:
            reasons[name].append('matching release tag ' + tag)
    elif changed is None or set(changed) & (set(data['sources']) | WORKSPACE_FILES):
        direct = set(projects)
        for name in direct:
            reasons[name].append('full event, unavailable diff, or shared configuration/lockfile change')
    else:
        direct = {n for n, p in projects.items() if any(matches_path(f, pattern) for f in changed for pattern in project_paths(p))}
        for name in direct:
            reasons[name].append('changed project path or watch pattern')
    selected = set(direct)
    if changed is not None and event != 'release':
        for name, deployment in data['pipeline']['deployments'].items():
            if event in deployment['workflows'] and any(matches_path(f, p) for f in changed for p in deployment_paths(deployment)):
                for binding in deployment['images']:
                    owner = binding['from_'].split('.')[0]
                    selected.add(owner)
                    reasons[owner].append('deployment input changed: ' + name)
    for candidate in candidates:
        for name in candidate['projects']:
            selected.add(name)
            reasons[name].append('candidate dependency: ' + candidate['package'])
    selected, active = closure(data, event, selected)
    for name in selected:
        if not reasons[name]:
            reasons[name].append('required by project, artifact, release or deployment dependency')
    return {'selected': sorted(selected), 'direct': sorted(direct), 'deployments': sorted(active),
            'reasons': {n: reasons[n] for n in sorted(selected)}}


def native_paths(data, event):
    """Conservatively propagate each source path through the same dependency closure."""
    projects = data['pipeline']['projects']
    by_project = {n: set() for n in projects}
    by_deployment = {n: set() for n in data['pipeline']['deployments']}
    seeds = [(project_paths(p), {n}) for n, p in projects.items()]
    seeds += [(deployment_paths(d), {i['from_'].split('.')[0] for i in d['images']})
              for d in data['pipeline']['deployments'].values() if event in d['workflows']]
    for paths, initial in seeds:
        selected, active = closure(data, event, initial)
        for name in selected:
            by_project[name].update(paths)
        for name in active:
            by_deployment[name].update(paths)
    common = set(data['sources']) | WORKSPACE_FILES
    result = {}
    for key, node in data['nodes'].items():
        if node['event'] != event:
            continue
        paths = common | (by_deployment[node['deployment']] if node['deployment'] else by_project[node['project']])
        result[key] = ['**/*'] if '**/*' in paths or len(paths) > 50 else sorted(paths)
    return result
