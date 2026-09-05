# Documentation map

Start with the [README quick start](../README.md#quick-start), then use the implementation guides below. The CLI compiles developer-owned workflows into GitLab CI. Existing components remain available separately.

| Need | Start here |
|---|---|
| Ask an AI to create/edit a pipeline | [AI-assisted authoring — revision three](ai-authoring-revision-three.md) |
| Install the portable agent skill | [Skill entrypoint](../skills/generic-ci-authoring/SKILL.md); copy the whole directory as explained in the AI guide |
| Understand accepted CLI commands and path rules | [CLI reference](cli-reference.md) |
| Configure organization templates/defaults once | [Configuration sources — revision two](configuration-sources-revision-two.md) |
| Define checks, monorepos, versions, packages and deployments | [Implemented workflows — revision one](workflows-revision-one.md) |
| Copy a small complete authoring example | [Workflow examples](../examples/workflows) |
| Configure an image-factory repository | [Image-factory starter](../starters/image-factory) and the workflow Buildah section |
| Add offline editor completion | [Workflow schema](../schemas/workflows.schema.json), [platform schema](../schemas/workflows-platform.schema.json), [source schema](../schemas/source.schema.json), [project source schema](../schemas/source-project.schema.json) |
| Use existing low-level GitLab components | [Component templates](../templates), [air-gap guide](airgap.md), [Kubernetes guide](kubernetes.md) |

Historical developer-experience proposals, product-contracts documents and scenario audits describe earlier design/review stages. Consult current implementation guides and installed schemas before applying their YAML. They are not evidence that every proposed feature is implemented or production-tested.

The CLI/source unit and integration tests establish local behavior. Live GitLab CI Lint, actual role-image execution, registry publication and cluster rollout are separate acceptance gates. The AI authoring guide includes realistic evaluation tasks; those are not a claim of completed independent agent evaluations.
