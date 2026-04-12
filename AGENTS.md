# AGENTS.md

Project-level instructions for coding agents working in this repository.

## Required close-out checks

1. Keep documentation and example configuration in sync with behavior changes.
2. Run the relevant verification before considering a task complete.

## Documentation rule

- If a change affects bot behavior, slash commands, setup, configuration, supported providers, deployment workflow, fork positioning, or upstream sync policy, update the relevant docs in the same task.
- At minimum, review whether changes are needed in:
  - `README.md`
  - `docs/PRODUCT_DIRECTION.md`
  - `docs/UPSTREAM.md`
  - `config-example.yaml`
  - `Dockerfile`
  - `docker-compose.yaml`
- Do not leave user-facing instructions or example config knowingly inconsistent with the implementation.

## Verification rule

- Always run the narrowest meaningful verification for the files you changed, then broaden only when the change warrants it.
- Examples:
  - `llmcord.py` changes: `python3 -m py_compile llmcord.py`
  - Dependency or packaging changes: review `requirements.txt` and validate the affected install or runtime path when practical
  - Docker or compose changes: `docker compose config`
- If a check cannot be run, state that explicitly in the final handoff and explain why.

## Working rule

- Prefer small, reviewable changes in this repo. It is a compact codebase, so avoid adding abstraction unless it clearly reduces complexity.
- Keep `llmcord.py`, `README.md`, and `config-example.yaml` aligned when adding features or changing commands/config behavior.
- Preserve compatibility with OpenAI-compatible providers unless the task explicitly scopes a breaking change.
- Treat this repository as an independent fork product, not a drop-in mirror of upstream. Keep the `agentcord` identity, release flow, and docs distinct even when syncing upstream fixes.
- Treat documentation and verification as part of the same change, not optional follow-up work.
- After any change that affects runtime behavior, config loading, dependencies, Docker packaging, or startup state, rebuild and/or restart the local Docker container or running app before handoff whenever required so testing is performed against the updated code, not a stale process.

## Release and commit rule

- Prefer conventional-commit style titles and commit messages for substantial changes:
  - `feat: ...`
  - `fix: ...`
  - `docs: ...`
  - `refactor: ...`
  - `chore: ...`
- Use `!` or `BREAKING CHANGE:` only for intentional breaking changes.
