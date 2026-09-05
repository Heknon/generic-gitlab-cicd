"""Public workflow schema. No hidden test names or raw GitLab rules."""
from typing import Literal
from pydantic import Field, model_validator, field_validator
from generic_ci.models import Model, Name, relative

class Python(Model):
    groups: Literal['all'] | list[str] | None = Field(default=None, description='Omit to respect uv defaults; all selects all groups, not all workspace members.')
    extras: Literal['all'] | list[str] = Field(default_factory=list)
    upgrade: Literal['none', 'all'] = 'none'

class Node(Model):
    package_manager: Literal['npm', 'pnpm', 'bun']
    workspace: str | None = Field(default=None, description='Explicit repository-relative workspace root; otherwise discover membership.')
    _path = field_validator('workspace')(lambda v: relative(v) if v is not None else v)

class Execution(Model):
    image: str | None = None
    tags: list[str] | None = None
    timeout: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    services: list[str] | None = None
    parallel: dict | None = None
    artifacts: dict = Field(default_factory=dict)
    @field_validator('variables')
    @classmethod
    def variables_safe(cls, v):
        if any(k.startswith(('CI_', 'TOOLKIT_')) or k in {'PYTHONPATH', 'PYTHONHOME', 'DEPLOYMENT_URL'} for k in v):
            raise ValueError('CI_*, TOOLKIT_*, Python bootstrap and DEPLOYMENT_URL variables are reserved')
        return v

class Check(Execution):
    script: list[str] = Field(min_length=1, description='Team-owned commands, executed after preparation.')
    dependencies: Python | Literal[False] | None = None
    needs: list[str] = Field(default_factory=list, description='Checks or project.build outputs required in the same event.')
    outputs: list[str] = Field(default_factory=list, description='Project-relative files passed to consumers; matrices cannot produce shared files.')
    _paths = field_validator('outputs')(lambda v: [relative(p) for p in v])

class Build(Model):
    script: list[str] = Field(min_length=1)
    outputs: list[str] = Field(min_length=1)
    needs: list[str] = Field(default_factory=list)
    _paths = field_validator('outputs')(lambda v: [relative(p) for p in v])

class Container(Model):
    dockerfile: str = 'Dockerfile'
    context: str = '.'
    repository: str | None = None
    needs: list[str] = Field(default_factory=list)
    build_args: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict, description='Secret ID to file-type CI variable name.')
    target: str | None = None
    dependency_bundle: bool = Field(default=False, description='Python Dockerfile consumes named ci-dependencies wheelhouse context; required for candidate previews.')
    _paths = field_validator('dockerfile', 'context')(relative)

class Package(Model):
    directory: str = '.'
    index: str | None = Field(default=None, description='Python named uv publishing index; Node reads publishConfig.registry.')
    needs: list[str] = Field(default_factory=list)
    _path = field_validator('directory')(relative)

class VersionSource(Model):
    file: str
    field: str
    _path = field_validator('file')(relative)

class CreateRelease(Model):
    branch: str = 'main'
    when: Literal['manual'] = 'manual'

class Release(Model):
    version: VersionSource | None = None
    require_bump: bool = True
    tag: str
    create: CreateRelease | None = None
    needs: list[str] = Field(default_factory=list, description='Other projects whose matching release publication must finish first.')
    @field_validator('tag')
    @classmethod
    def pattern(cls, v):
        if v.count('{version}') != 1 or '{' in v.replace('{version}', ''):
            raise ValueError('tag requires exactly one {version}')
        return v

class Workflow(Model):
    checks: list[str] = Field(default_factory=list)
    build: bool | list[Literal['application', 'container', 'package']] = False
    publish: bool = False

Event = Literal['push', 'merge-request', 'release', 'manual', 'schedule']

class Project(Model):
    path: str = '.'
    python: Python | None = None
    node: Node | None = None
    defaults: Execution = Field(default_factory=Execution)
    checks: dict[Name, Check] = Field(default_factory=dict)
    build: Build | None = None
    container: Container | None = None
    package: Package | None = None
    release: Release | None = None
    workflows: dict[Event, Workflow] = Field(default_factory=dict)
    depends_on: list[Name] = Field(default_factory=list, description='Changes to these projects select this consumer. Installation follows package metadata.')
    watch: list[str] = Field(default_factory=list)
    _paths = field_validator('path')(relative)
    @model_validator(mode='after')
    def ecosystem(self):
        if self.python is not None and self.node is not None:
            raise ValueError('use separate project identities for Python and Node outputs sharing a path')
        if self.release and not self.release.version:
            if self.python is not None:
                self.release.version = VersionSource(file='pyproject.toml', field='project.version')
            elif self.node is not None:
                self.release.version = VersionSource(file='package.json', field='version')
            else:
                raise ValueError('release.version is required without a Python or Node ecosystem')
        return self

class Chart(Model):
    path: str | None = None
    repository: str | None = None
    name: str | None = None
    oci: str | None = None
    version: str | None = None
    @model_validator(mode='after')
    def source(self):
        if sum(bool(v) for v in (self.path, self.repository, self.oci)) != 1:
            raise ValueError('choose exactly one chart source: path, repository, oci')
        if self.path:
            relative(self.path)
            if self.name or self.version:
                raise ValueError('local chart cannot specify name/version')
        elif not self.version or (self.repository and not self.name):
            raise ValueError('remote chart requires pinned version; Helm repository also requires name')
        if self.oci and (not self.oci.startswith('oci://') or self.name):
            raise ValueError('OCI chart requires oci:// reference and no name')
        return self

class Binding(Model):
    repository: str | None = None
    tag: str | None = None
    digest: str | None = None
    @model_validator(mode='after')
    def paths(self):
        import re
        if not self.tag and not self.digest:
            raise ValueError('map at least tag or digest')
        for v in (self.repository, self.tag, self.digest):
            if v and not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', v):
                raise ValueError('image paths support dotted mapping keys; array indices and literal dots are not supported')
        return self

class Image(Model):
    from_: str = Field(alias='from')
    set: Binding

class CheckList(Model):
    checks: list[str] = Field(default_factory=list)

class DeployWorkflow(Model):
    when: Literal['manual', 'automatic'] = 'manual'

class Deployment(Model):
    target: str = Field(description='Platform target containing namespace, kubeconfig variable and URL.')
    chart: Chart
    values: list[str] = Field(default_factory=list)
    images: list[Image] = Field(min_length=1)
    update: Literal['complete', 'partial'] = 'complete'
    before: CheckList = Field(default_factory=CheckList)
    after: CheckList = Field(default_factory=CheckList)
    workflows: dict[Literal['release', 'merge-request'], DeployWorkflow] = Field(default_factory=dict)
    auto_stop_in: str = '2 days'
    _paths = field_validator('values')(lambda v: [relative(p) for p in v])

class Pipeline(Model):
    version: Literal[1] = 1
    projects: dict[Name, Project] = Field(min_length=1)
    deployments: dict[Name, Deployment] = Field(default_factory=dict)

class Builder(Model):
    engine: Literal['buildah'] = 'buildah'
    image: str
    tags: list[str] | None = None

class Registries(Model):
    containers: str
    previews: str

class Target(Model):
    namespace: str
    kubeconfig_variable: str = 'KUBECONFIG'
    url: str = ''
    production: bool = True
    release: str | None = None

class Platform(Model):
    version: Literal[1] = 1
    defaults: Execution = Field(default_factory=Execution)
    images: dict[str, str] = Field(description='Prepared images for python, node, bun, helm, and optional control role; all include toolkit Python runtime.')
    container_builder: Builder
    registries: Registries
    allowed_hosts: list[str] = Field(min_length=1)
    targets: dict[Name, Target] = Field(default_factory=dict)
    variables: dict[str, str] = Field(default_factory=dict)
    max_jobs: int = Field(default=150, ge=1, le=1000)
