# Product Direction

`agentcord` is the maintained advanced fork of the original `llmcord` project.

## Positioning

This fork keeps the core idea of the upstream project intact:
- Discord as a lightweight frontend for LLM conversations
- support for OpenAI-compatible providers
- minimal deployment surface

This fork intentionally extends that base into a more operationally mature distribution.

## What This Fork Adds

- Runtime admin controls for model switching, system prompt management, command sync, and config reloads
- Persistent system prompt updates written back to `config.yaml`
- Improved user identity handling for providers that support OpenAI-style message names
- Better compatibility across `discord.py` UI variants for plain responses
- First-party Docker packaging for local deployment
- GitHub Actions for SemVer releases and GHCR image publishing
- Stronger repository rules for docs, verification, and independent release management

## Product Rules

- Upstream compatibility matters, but this fork is allowed to diverge when the advanced feature set benefits from it.
- New features should prefer operational control, observability, packaging, and provider flexibility over preserving strict minimalism.
- Changes that alter runtime behavior should update `README.md`, `config-example.yaml`, and any relevant docs in `docs/`.
- Release automation and container publishing are part of the product surface, not optional extras.

## Near-Term Roadmap

- Expand admin/runtime management without requiring bot restarts
- Improve provider-specific metadata handling while preserving OpenAI-compatible requests
- Add more robust validation and targeted tests around conversation assembly and command behavior
- Continue packaging the fork as a clean self-hosted Discord bot distribution
