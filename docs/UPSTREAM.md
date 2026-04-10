# Upstream Strategy

This repository is an independent fork of the original `llmcord` project:

- Fork remote: `origin` -> `https://github.com/V5U2/llmcord`
- Upstream remote: `upstream` -> `https://github.com/jakobdylanc/llmcord`

## Fork Model

This is a periodically synced fork, not a strict mirror.

That means:
- upstream fixes and improvements can still be pulled in
- this repo owns its own release cadence, container images, and feature direction
- divergence is acceptable when needed for the advanced distribution

## Recommended Sync Workflow

Fetch upstream changes:

```bash
git fetch upstream
```

Inspect divergence:

```bash
git log --oneline --left-right --graph upstream/main...main
```

Merge upstream into the fork:

```bash
git checkout main
git merge upstream/main
```

Or rebase a feature branch on top of the updated fork main:

```bash
git checkout my-branch
git rebase main
```

## Merge Policy

- Prefer preserving advanced-fork features when upstream and fork behavior conflict.
- Re-run packaging, docs, and release checks after upstream syncs.
- Update `README.md` when upstream-derived behavior changes the user-facing story of the fork.
