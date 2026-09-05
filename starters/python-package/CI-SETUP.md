# CI starter

Configure your organization source defaults before adoption. The bundled Artifactory addresses are examples. Provide runner authentication and trusted certificates through your platform setup.

Run `generic-ci validate`, then `generic-ci render -o .gitlab-ci.yml`. Commit delivery.yml, generic-ci.yml, generic-ci.lock.json and generated CI.

Python/Node starters expect an existing application, its tests, committed lockfile and (for Node services) Dockerfile. The image factory starter supplies example build inputs. Review all commands before running CI.
