# Scripts

This directory is intentionally small.

- `ollama-entrypoint.sh` is used by `docker-compose.yml` for the local Ollama service.
- `research/` contains exploratory transcript segmentation, moderator extraction, minutes-generation prototypes, and a 70B Kubernetes experiment. Those files are not part of the production runtime path.

Do not add one-off analysis scripts to this directory root. Put them under `scripts/research/` and document required local data, credentials, and expected outputs there.
