"""Toolkit vocabulary. Native job execution fields retain their GitLab meanings."""
from typing import Annotated, Any, Literal
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True,
                              alias_generator=lambda value: value.replace("_", "-"))


def relative(value: str) -> str:
    if not value or "\\" in value or "$" in value or ":" in value:
        raise ValueError("use a literal POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must remain inside the repository")
    return value


class RootPath(Model):
    repository: str = Field(description="Explicit path relative to the repository root.")
    _path = field_validator("repository")(relative)


class Dependencies(Model):
    manager: Literal["uv", "none"] = "uv"
    upgrade: Literal["none", "all"] | list[str] = Field(default="none", description="Upgrade policy within project constraints; changes are temporary.")
    groups: list[str] = Field(default_factory=lambda: ["*"], description="Dependency groups; * selects all, [] excludes dev groups.")
    extras: list[str] = Field(default_factory=list)
    python: str | None = Field(default=None, description="Required interpreter version, verified without downloading Python.")


class NativeJob(Model):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True, alias_generator=None)
    image: str | None = None
    tags: list[str] | None = None
    services: list[Any] | None = None
    parallel: dict[str, Any] | None = Field(default=None, description="Native parallel:matrix; compiled into explicit jobs for receipt identity.")
    rules: list[dict[str, Any]] | None = None
    timeout: str | None = None
    cache: dict[str, Any] | None = None
    resource_group: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    allow_failure: bool = False

    @field_validator("variables")
    @classmethod
    def reserved(cls, value):
        if any(k.startswith(("CI_", "TOOLKIT_")) or k in {"PYTHONPATH", "PYTHONHOME"} for k in value):
            raise ValueError("CI_*, TOOLKIT_* and Python bootstrap variables are reserved")
        return value


class Check(Model):
    runtime: str = Field(default="default", description="Platform runtime profile; selects tools and shell, independently of runner tags.")
    script: list[str] | None = Field(default=None, min_length=1, description="Commands after dependency setup; omit only when inheriting a preset check.")
    dependencies: Dependencies | None = None
    gitlab: NativeJob = Field(default_factory=NativeJob)
    needs: list[str] = Field(default_factory=list, description="Public step references: step or project.step.")
    junit: list[str] = Field(default_factory=list, description="Project-relative report paths.")
    _paths = field_validator("junit")(lambda paths: [relative(p) for p in paths])


class Step(Check):
    script: list[str] = Field(min_length=1)
    outputs: dict[Name, str] = Field(default_factory=dict, description="Named files/directories passed to dependent steps and builds.")
    _output_paths = field_validator("outputs")(lambda paths: {k: relative(v) for k, v in paths.items()})


class Package(Model):
    directory: str = Field(default=".", description="Package build directory relative to project.path; useful for generated SDKs.")
    index: str | None = Field(default=None, description="Named tool.uv.index in pyproject; required to publish.")
    needs: list[str] = Field(default_factory=list)
    checks: list[str] | None = None
    _directory = field_validator("directory")(relative)


class Container(Model):
    dockerfile: str = "Dockerfile"
    context: str | RootPath = "."
    repository: str | None = None
    platform: str = "linux/amd64"
    target: str | None = Field(default=None, description="Optional Dockerfile build stage.")
    build_args: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict, description="BuildKit secret ID to file-type CI variable name.")
    needs: list[str] = Field(default_factory=list)
    checks: list[str] | None = None
    dependency_bundle: bool = Field(default=False, description="Dockerfile explicitly consumes named ci-dependencies context (see recipe).")
    _dockerfile = field_validator("dockerfile")(relative)

    @field_validator("context")
    @classmethod
    def path(cls, value):
        return relative(value) if isinstance(value, str) else value


class ImageBinding(Model):
    repository: list[str] = Field(min_length=1, description="Path in chart values for image repository.")
    digest: list[str] = Field(min_length=1, description="Path in chart values for image sha256 digest.")


class Deployment(Model):
    target: str
    chart: str | None = None
    chart_version: str | None = None
    values: list[str] = Field(default_factory=list, description="Project-relative values files, applied in order.")
    checks: list[str] | None = Field(default=None, description="Required source checks; omitted inherits all enabled project checks, [] opts out.")
    before: list[str] = Field(default_factory=list, description="Fresh commands after approval and before rollout, e.g. migrations.")
    after: list[str] = Field(default_factory=list, description="Fresh commands after rollout; failure marks deployment failed.")
    images: dict[str, ImageBinding] = Field(default_factory=dict, description="Container project to chart values binding; stock chart defaults to own container.")
    preview: bool = False
    auto_stop_in: str = "2 days"
    when: Literal["manual", "on_success"] = "manual"
    _values = field_validator("values")(lambda paths: [relative(p) for p in paths])


class Release(Model):
    tag: str = Field(default="v{version}", description="Existing tag pattern; exactly one {version} placeholder.")
    version_file: str = "pyproject.toml"
    bump: bool = True
    needs: list[str] = Field(default_factory=list, description="Projects whose publication must complete first.")
    notes: str = "CHANGELOG.md"
    gitlab_release: bool = True
    _version = field_validator("version_file", "notes")(relative)

    @field_validator("tag")
    @classmethod
    def pattern(cls, value):
        if value.count("{version}") != 1 or "{" in value.replace("{version}", ""):
            raise ValueError("tag must contain exactly one {version}")
        return value


class Project(Model):
    path: str = "."
    preset: Literal["generic", "python-package", "python-service"] = "generic"
    dependencies: Dependencies | None = None
    checks: dict[Name, Check | Literal[False]] = Field(default_factory=dict)
    steps: dict[Name, Step] = Field(default_factory=dict)
    package: Package | None = None
    container: Container | None = None
    deploy: dict[Name, Deployment] = Field(default_factory=dict)
    release: Release | None = None
    depends_on: list[str] = Field(default_factory=list, description="Project dependencies used for transitive changed-only selection.")
    watch: list[str] = Field(default_factory=list, description="Additional repository-relative changed paths/globs.")
    _path = field_validator("path")(relative)


class Pipeline(Model):
    schema_version: Literal[1] = 1
    projects: dict[Name, Project] = Field(min_length=1)


class Runtime(Model):
    image: str | None = Field(default=None, description="Approved image with generic-gitlab-ci at the compiler version plus required tools.")
    tags: list[str] = Field(default_factory=list)
    shell: Literal["sh", "powershell"] = "sh"


class Target(Model):
    namespace: str
    release_prefix: str = "app"
    url: str = ""
    production: bool = True
    kubeconfig_variable: str = "KUBECONFIG"


class Platform(Model):
    schema_version: Literal[1] = 1
    runtimes: dict[str, Runtime] = Field(description="Must include default; optional build, helm, release runtime overrides.")
    registry: str
    preview_registry: str
    chart: str
    chart_version: str
    targets: dict[str, Target] = Field(default_factory=dict)
    allowed_hosts: list[str] = Field(min_length=1, description="Internal Git, registry, index and chart hosts; no implicit public fallback.")
    max_jobs: int = Field(default=150, ge=1, le=1000)
    artifact_retention: str = "30 days"
    mandatory_checks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def default_runtime(self):
        if "default" not in self.runtimes:
            raise ValueError("runtimes.default is required")
        return self
