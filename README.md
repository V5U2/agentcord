<h1 align="center">
  agentcord
</h1>

<h3 align="center"><i>
  Discord as an LLM frontend, extended into an advanced self-hosted fork.
</i></h3>

<p align="center">
  <img src="https://github.com/user-attachments/assets/7791cc6b-6755-484f-a9e3-0707765b081f" alt="">
</p>

`agentcord` is an independent fork of the original `llmcord` project. It keeps the same core idea, Discord as a collaborative frontend for LLMs, but builds on it with stronger runtime controls, safer secret handling, bounded memory, safe built-in tool use, and an opinionated release path for self-hosting.

The original upstream project is [jakobdylanc/llmcord](https://github.com/jakobdylanc/llmcord). This fork is maintained at [V5U2/agentcord](https://github.com/V5U2/agentcord).

## What This Fork Adds

- Live admin commands for model switching, system prompt management, config reloads, and command syncing
- Persistent system prompt overrides stored under the memory root instead of rewriting `config.yaml`
- Better provider-specific user identity handling for OpenAI-style `name` fields
- Configurable secret indirection via env vars, files, or Codex auth-file API key reuse
- Small typed user memory with delete controls
- Safe built-in skills for read-only web search/fetch with server-side allowlists and limits
- Docker packaging and compose-based local deployment
- GitHub Actions for SemVer releases and container publishing to GHCR
- Explicit upstream-sync and fork-maintenance docs for long-term ownership

## Features

### Reply-based chat system:
Just @ the bot to start a conversation and reply to continue. Build conversations with reply chains!

You can:
- Branch conversations endlessly
- Continue other people's conversations
- @ the bot while replying to ANY message to include it in the conversation

Additionally:
- When DMing the bot, conversations continue automatically (no reply required). To start a fresh conversation, just @ the bot. You can still reply to continue from anywhere.
- You can branch conversations into [threads](https://support.discord.com/hc/en-us/articles/4403205878423-Threads-FAQ). Just create a thread from any message and @ the bot inside to continue.
- Back-to-back messages from the same user are automatically chained together. Just reply to the latest one and the bot will see all of them.

---

### Model switching with `/model`:
![image](https://github.com/user-attachments/assets/568e2f5c-bf32-4b77-ab57-198d9120f3d2)

agentcord supports remote models from:
- [OpenAI API](https://platform.openai.com/docs/models)
- [xAI API](https://docs.x.ai/docs/models)
- [Google Gemini API](https://ai.google.dev/gemini-api/docs/models)
- [Mistral API](https://docs.mistral.ai/getting-started/models/models_overview)
- [Groq API](https://console.groq.com/docs/models)
- [OpenRouter API](https://openrouter.ai/models)

Or run local models with:
- [Ollama](https://ollama.com)
- [LM Studio](https://lmstudio.ai)
- [vLLM](https://github.com/vllm-project/vllm)

...Or use any other OpenAI compatible API server.

---

### Runtime admin commands:

Admins can manage the bot without restarting it:
- `/model` to switch the active model
- `/system_prompt` to view or update the live system prompt
- `/show_system_prompt` to view the full stored system prompt
- `/reload_config` to reload `config.yaml`
- `/sync_commands` to force Discord slash command sync
- `/memory` to inspect the small typed facts stored about your user
- `/forget_memory` to delete those stored facts

System prompt changes made through `/system_prompt` are persisted under `data/memory/`, which keeps writable state inside the memory store instead of modifying `config.yaml`.

---

### And more:
- Supports image attachments when using a vision model (like gpt-5, grok-4, claude-4, etc.)
- Supports text file attachments (.txt, .py, .c, etc.)
- Customizable personality (aka system prompt), including live updates from Discord
- Distinguishes users via their Discord IDs, with native per-user message names for supported providers
- Supports bounded typed memory for small user facts such as preferred names and likes/dislikes
- Supports safe built-in skills like read-only web search/fetch through server-side allowlists
- Keeps provider secrets out of model-visible context through env/file indirection and redaction-safe handling
- Streamed responses (turns green when complete, automatically splits into separate messages when too long)
- Hot reloading config (you can change settings without restarting the bot)
- Compatible with older and newer `discord.py` UI APIs when using plain responses
- Displays helpful warnings when appropriate (like "⚠️ Only using last 25 messages" when the customizable message limit is exceeded)
- Caches message data in a size-managed (no memory leaks) and mutex-protected (no race conditions) global dictionary to maximize efficiency and minimize Discord API calls
- Fully asynchronous
- 1 Python file, ~300 lines of code

## Instructions

1. Clone the repo:
   ```bash
   git clone https://github.com/V5U2/agentcord
   cd agentcord
   ```

2. Create a copy of `config-example.yaml` named `config.yaml` and set it up:

### Discord settings:

| Setting | Description |
| --- | --- |
| **bot_token** | Create a new Discord bot at [discord.com/developers/applications](https://discord.com/developers/applications) and generate a token under the "Bot" tab. Also enable "MESSAGE CONTENT INTENT". |
| **client_id** | Found under the "OAuth2" tab of the Discord bot you just made. |
| **status_message** | Set a custom message that displays on the bot's Discord profile.<br /><br />**Max 128 characters.** |
| **max_text** | The maximum amount of text allowed in a single message, including text from file attachments.<br /><br />Default: `100,000` |
| **max_images** | The maximum number of image attachments allowed in a single message.<br /><br />Default: `5`<br /><br />**Only applicable when using a vision model.** |
| **max_messages** | The maximum number of messages allowed in a reply chain. When exceeded, the oldest messages are dropped.<br /><br />Default: `25` |
| **use_plain_responses** | When set to `true` the bot will use plaintext responses instead of embeds. Plaintext responses have a shorter character limit so the bot's messages may split more often.<br /><br />Default: `false`<br /><br />**Also disables streamed responses and warning messages.** |
| **allow_dms** | Set to `false` to disable direct message access.<br /><br />Default: `true` |
| **wake_names** | Optional names that can wake the bot in public channels without an @ mention, for example `agentcord`. Matching is case-insensitive and requires a boundary around the configured name so it does not match inside longer words. |
| **features** | Feature flags for `tools`, `memory`, and `codex_auth_file` integration paths. Keep `codex_auth_file` disabled unless you have explicitly mounted or configured a Codex auth file for your deployment. |
| **permissions** | Configure access permissions for `users`, `roles` and `channels`, each with a list of `allowed_ids` and `blocked_ids`.<br /><br />Control which `users` are admins with `admin_ids`. Admins can change the model with `/model`, manage the system prompt with `/system_prompt` and `/show_system_prompt`, reload `config.yaml` with `/reload_config`, sync slash commands with `/sync_commands`, and DM the bot even if `allow_dms` is `false`.<br /><br />**Leave `allowed_ids` empty to allow ALL in that category.**<br /><br />**Role and channel permissions do not affect DMs.**<br /><br />**You can use [category](https://support.discord.com/hc/en-us/articles/115001580171-Channel-Categories-101) IDs to control channel permissions in groups.** |

### LLM settings:

| Setting | Description |
| --- | --- |
| **providers** | Add the LLM providers you want to use, each with a `base_url` and one secret-loading path: inline `api_key`, `api_key_env`, `api_key_file`, or `auth_mode: codex_auth_file_api_key` with `codex_auth_file`. Set `supports_tools: true` only for providers/models where you want safe skill execution enabled.<br /><br />**Only supports OpenAI compatible APIs.**<br /><br />**Some providers may need `extra_headers` / `extra_query` / `extra_body` entries for extra HTTP data. See the included `azure-openai` provider for an example.** |
| **models** | Add the models you want to use in `<provider>/<model>: <parameters>` format (examples are included). When you run `/model` these models will show up as autocomplete suggestions.<br /><br />**Refer to each provider's documentation for supported parameters.**<br /><br />**The first model in your `models` list will be the default model at startup.**<br /><br />**Some vision models may need `:vision` added to the end of their name to enable image support.** |
| **system_prompt** | Write anything you want to customize the bot's behavior!<br /><br />**Leave blank for no system prompt.**<br /><br />**You can use the `{date}` and `{time}` tags in your system prompt to insert the current date and time, based on your host computer's time zone.**<br /><br />This value is loaded from `config.yaml` at startup. Live updates from `/system_prompt` are stored under `data/memory/system/` so the runtime only writes inside the memory root.<br /><br />For providers that support OpenAI-style message names (currently `openai` and `x-ai`), `agentcord` sends Discord user IDs in the `name` field and automatically appends guidance telling the model to mention users as `<@ID>`. For other providers, it is still recommended to include something like `"User messages are prefixed with their Discord ID as <@ID>. Use this format to mention users."` in your system prompt. |
| **tools** | Safe built-in skills. `web_search` uses the DuckDuckGo instant-answer backend through an allowlisted host. `web_fetch` is optional and only works for explicitly allowlisted hosts. All tool execution is read-only and bounded by host, timeout, byte, and result-count limits. |
| **tool_max_rounds** | Maximum number of tool-execution rounds per assistant turn. Default: `3`. |

3. Run the bot:

   **No Docker:**
   ```bash
   python -m pip install -U -r requirements.txt
   python llmcord.py
   ```

   **With Docker:**
   ```bash
   docker compose up
   ```

   The compose setup mounts `./config.yaml` read-only and mounts `./data/memory` as the only writable application state.

   If you use local providers such as Ollama, LM Studio, or vLLM on the host machine while llmcord runs in Docker, change their `base_url` values from `localhost` to `host.docker.internal`.

   If you want to reuse the OpenAI API key that Codex writes locally after ChatGPT sign-in, configure the OpenAI provider with `auth_mode: codex_auth_file_api_key` and point `codex_auth_file` at your Codex auth file, typically `~/.codex/auth.json`. agentcord reads the generated `OPENAI_API_KEY` from that file; it does not use Codex OAuth access or refresh tokens directly.

   If your Codex auth file only has ChatGPT tokens and no generated `OPENAI_API_KEY`, you can test the experimental `auth_mode: codex_chatgpt_token` path. This passes `tokens.access_token` as the bearer credential and is intentionally feature-flagged because OpenAI’s public docs do not currently describe it as a supported third-party/server-hosted API auth mode.

   In Docker, that auth file is not mounted by default. Add a read-only bind mount such as `~/.codex/auth.json:/app/secrets/codex-auth.json:ro` and set `codex_auth_file: /app/secrets/codex-auth.json`.

## Docker image

- Local build:
  ```bash
  docker build -t agentcord:local .
  ```
- Local run:
    ```bash
    docker run --rm \
      --read-only \
      --add-host host.docker.internal:host-gateway \
      -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
      -v "$(pwd)/data/memory:/app/data/memory" \
      agentcord:local
    ```
- GitHub Actions publishes images to `ghcr.io/v5u2/agentcord`.

## Releases

- `.github/workflows/release-please.yml` runs on pushes to `main` and manages SemVer GitHub releases using conventional commits.
- `.github/workflows/docker-image.yml` builds the container on pull requests and on `main`/tag/release events, and publishes to GHCR for non-PR runs.
- To get predictable version bumps, use conventional commit prefixes such as `feat:`, `fix:`, `docs:`, `refactor:`, and `chore:`.

## Upstream Relationship

- This repo is a periodically synced fork, not a strict mirror.
- `origin` is your maintained fork: `https://github.com/V5U2/agentcord`
- `upstream` tracks the original project: `https://github.com/jakobdylanc/llmcord`
- See [docs/UPSTREAM.md](/Users/james/Documents/Development/llmcord/docs/UPSTREAM.md) for the sync policy and workflow.
- See [docs/PRODUCT_DIRECTION.md](/Users/james/Documents/Development/llmcord/docs/PRODUCT_DIRECTION.md) for `agentcord`’s product goals.

## Notes

- If you hit a problem, open or review issues in [V5U2/agentcord issues](https://github.com/V5U2/agentcord/issues).

- PRs are welcome :)

## Star History

<a href="https://star-history.com/#V5U2/agentcord&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=V5U2/agentcord&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=V5U2/agentcord&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=V5U2/agentcord&type=Date" />
  </picture>
</a>
