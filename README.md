# agentcord

`agentcord` is a self-hosted Discord frontend for OpenAI-compatible LLM providers. It is an independent fork of [`jakobdylanc/llmcord`](https://github.com/jakobdylanc/llmcord) that keeps the reply-chain chat model and adds safer runtime controls, bounded memory, server-side tool policy, hardened container deployment, and release automation.

The maintained fork is [`V5U2/agentcord`](https://github.com/V5U2/agentcord).

## Features

- Reply-chain conversations in Discord channels, threads, and DMs
- Slash-command model switching and config reloads
- Admin-only live system prompt overrides stored under `data/memory`
- Optional wake names so the bot can respond without an `@` mention
- Optional channel-scoped clanker mode for playful bot-to-bot rivalry
- OpenAI-compatible provider support, including OpenAI, OpenRouter, xAI, Gemini-compatible endpoints, Ollama, LM Studio, and vLLM
- Secret loading through inline config, environment variables, files, Codex auth-file API key reuse, or experimental Codex ChatGPT access-token reuse
- Bounded typed user memory with `/memory` and `/forget_memory`
- Safe tool support:
  - OpenRouter server-side `openrouter:web_search`
  - Local Firecrawl or DuckDuckGo instant-answer search backend for non-OpenRouter providers
  - Configured RSS/Atom feed reader with friendly feed names
  - Optional allowlisted web fetch
- Docker and Unraid deployment support with read-only root filesystem and memory-only writable state

## Quick Start

```bash
git clone https://github.com/V5U2/agentcord
cd agentcord
cp config-example.yaml config.yaml
python -m pip install -U -r requirements.txt
python llmcord.py
```

Create a Discord bot at [discord.com/developers/applications](https://discord.com/developers/applications), enable `MESSAGE CONTENT INTENT`, and put the bot token in `config.yaml`.

## Docker

```bash
docker compose up
```

The compose file mounts:

- `./config.yaml` -> `/app/config.yaml:ro`
- `./data/memory` -> `/app/data/memory`
- `./data/codex` -> `/app/secrets/codex:ro`

The container runs with a read-only root filesystem and `/tmp` as tmpfs. Runtime writes are intentionally constrained to `data/memory`.

Manual run:

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -v "$(pwd)/data/memory:/app/data/memory" \
  ghcr.io/v5u2/agentcord:latest
```

If the bot needs to reach host-local model servers such as Ollama, LM Studio, or vLLM from Docker, use `host.docker.internal` in provider `base_url` values.

### Codex ChatGPT Sign-In From Docker Logs

The main `agentcord` image does not include Codex or Node. For one-time Codex sign-in, use the separate Compose auth profile:

```bash
docker compose --profile auth up codex-auth
```

Follow the login URL/code shown in the container logs:

```bash
docker compose logs -f codex-auth
```

After login completes, the auth file is written to:

```text
./data/codex/auth.json
```

Configure the OpenAI provider to read it inside the `agentcord` container:

```yaml
features:
  codex_auth_file: true

providers:
  openai:
    base_url: https://api.openai.com/v1
    auth_mode: codex_chatgpt_token
    codex_auth_file: /app/secrets/codex/auth.json
```

Use `auth_mode: codex_auth_file_api_key` instead if the auth file contains `OPENAI_API_KEY`.

## Unraid

An Unraid Docker template is available at [`templates/agentcord-unraid.xml`](templates/agentcord-unraid.xml).

Recommended Unraid paths:

- Config: `/mnt/user/appdata/agentcord/config.yaml` -> `/app/config.yaml:ro`
- Memory: `/mnt/user/appdata/agentcord/memory` -> `/app/data/memory`
- Optional Codex auth file: mount read-only to `/app/secrets/codex/auth.json`

If you mount a Codex auth file in Unraid, set:

```yaml
providers:
  openai:
    auth_mode: codex_auth_file_api_key
    codex_auth_file: /app/secrets/codex/auth.json
```

## Configuration

Start from [`config-example.yaml`](config-example.yaml). The main sections are:

```yaml
bot_token:
client_id:
status_message:

wake_names:
  - agentcord

features:
  tools: true
  memory: true
  codex_auth_file: false
  clanker_mode: false
```

### Permissions

`permissions.users.admin_ids` controls admin-only commands:

- `/model`
- `/system_prompt`
- `/show_system_prompt`
- `/reload_config`
- `/sync_commands`
- `/clanker_add`
- `/clanker_remove`
- `/clankers`

Allowed/blocked users, roles, and channels are configured under:

```yaml
permissions:
  users:
    admin_ids: []
    allowed_ids: []
    blocked_ids: []
  roles:
    allowed_ids: []
    blocked_ids: []
  channels:
    allowed_ids: []
    blocked_ids: []
```

Leave `allowed_ids` empty to allow all entries in that category.

### Providers and Secrets

Providers are configured under `providers`. Each provider needs a `base_url` and one credential source.

Inline key:

```yaml
providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_key: sk-or-...
```

Environment variable:

```yaml
providers:
  openai:
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
```

File:

```yaml
providers:
  openai:
    base_url: https://api.openai.com/v1
    api_key_file: /run/secrets/openai_api_key
```

Codex auth-file API key reuse:

```yaml
features:
  codex_auth_file: true

providers:
  openai:
    base_url: https://api.openai.com/v1
    auth_mode: codex_auth_file_api_key
    codex_auth_file: ~/.codex/auth.json
```

Experimental Codex ChatGPT token reuse:

```yaml
features:
  codex_auth_file: true

providers:
  openai:
    base_url: https://api.openai.com/v1
    auth_mode: codex_chatgpt_token
    codex_auth_file: ~/.codex/auth.json
```

`codex_chatgpt_token` passes `tokens.access_token` from the Codex auth file as the bearer credential. OpenAI public docs do not currently describe this as a supported third-party/server-hosted API auth mode, so treat it as experimental.

### Models

The first entry in `models` is used at startup:

```yaml
models:
  openrouter/x-ai/grok-4:

  openai/gpt-5:
    reasoning_effort: high
    verbosity: medium
```

Add `:vision` to a model key when image attachments should be enabled for a provider/model that needs explicit vision tagging.

## Tools

Tool use is server-side gated. A tool must be enabled in config, the active provider must have `supports_tools: true`, and the execution path re-checks config before running local tools.

### OpenRouter Web Search

For OpenRouter, use the server-side search tool documented by OpenRouter:

```yaml
providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    supports_tools: true
    api_key: sk-or-...

tools:
  web_search:
    enabled: true
    backend: openrouter_server
    engine: exa
    max_results: 5
    max_total_results: 10
```

This sends:

```json
{"type": "openrouter:web_search"}
```

to OpenRouter. OpenRouter executes the search; `agentcord` does not run a local search request for that path.

### Firecrawl Search

For non-OpenRouter providers, use the local broker with Firecrawl:

```yaml
tools:
  web_search:
    enabled: true
    backend: firecrawl
    firecrawl_api_key_env: FIRECRAWL_API_KEY
    allowed_hosts:
      - api.firecrawl.dev
    sources:
      - web
      - news
    max_results: 5
```

Set `FIRECRAWL_API_KEY` in the runtime environment.

### RSS Feeds

RSS feeds are configured by friendly name so the model can choose the most relevant source:

```yaml
tools:
  rss_feed:
    enabled: true
    allowed_hosts:
      - feeds.bbci.co.uk
      - www.abc.net.au
    max_items: 5
    feeds:
      bbc_world:
        url: https://feeds.bbci.co.uk/news/world/rss.xml
        description: BBC World News
      abc_just_in:
        url: https://www.abc.net.au/news/feed/51120/rss.xml
        description: ABC News Just In
```

Example prompt:

```text
agentcord check bbc_world and summarize the top stories
```

### Web Fetch

`web_fetch` is disabled by default. If enabled, it only fetches explicitly allowlisted hosts and blocks local/private addresses.

```yaml
tools:
  web_fetch:
    enabled: false
    allowed_hosts: []
```

## Memory

Memory is enabled with:

```yaml
features:
  memory: true
```

Model-assisted extraction for new memory writes:

```yaml
memory:
  model_assisted: true
  ttl_days: 30
  input_max_chars: 1200
  context_message_lookback: 4
  allowed_fact_types:
    - preferred_name
    - nickname
    - pronouns
    - likes
    - dislikes
    - timezone
    - favorite_team
    - favorite_game
    - location
    - work_context
    - personal_context
    - preference
    - interests
    - hobbies
    - goals
    - projects
    - role
    - expertise
    - communication_style
  extraction_prompt: |
    Extract only small, non-sensitive user memory facts...
    Allowed types:
    {allowed_fact_types}
```

The memory subsystem stores small typed facts only. It does not store raw chat transcripts and does not allow the model to write arbitrary files.

When `memory.model_assisted: true`, the active model gets a bounded extraction pass that can propose additional memory candidates, but only into the existing typed schema. If `memory.model_assisted` is `false`, the current implementation does not write new memory facts. That extraction pass now includes:

- the latest triggering message
- the last `memory.context_message_lookback` text messages from the same user in that channel before it
- the current user ID as the target identity

This gives the model more conversation grounding while still limiting what can be remembered.

Allowed output schema:

- `preferred_name`
- `nickname`
- `pronouns`
- `likes`
- `dislikes`
- `timezone`
- `favorite_team`
- `favorite_game`
- `location`
- `work_context`
- `personal_context`
- `preference`
- `interests`
- `hobbies`
- `goals`
- `projects`
- `role`
- `expertise`
- `communication_style`

Those candidates are validated, deduplicated, length-limited, and then saved. The model is allowed to be somewhat looser about inferring stable preferences or context from the bounded recent conversation window, but it still cannot write arbitrary memory outside the approved schema.

Both the allowed fact types and the extraction guidance are configurable through `memory.allowed_fact_types` and `memory.extraction_prompt`. The `{allowed_fact_types}` placeholder is filled automatically when the extraction prompt is sent to the model, so you do not need to duplicate the list manually inside the prompt text.

Memory is scoped by user and guild/DM and stored under `data/memory`. Users can inspect or delete their own memory:

```text
/memory
/forget_memory
/forget_memory match:James
```

## System Prompt Overrides

`personality_prompt` is the editable personality layer loaded from `config.yaml`. Admins can override that live with:

```text
/system_prompt
/show_system_prompt
```

Runtime overrides are stored under `data/memory/system/` rather than rewriting `config.yaml`.

Operational instructions such as list formatting, Discord ID mention rules, and clanker-mode instructions are reconstructed separately at request time. They do not need to be baked into the personality prompt.

## Wake Names

`wake_names` lets the bot respond in public channels without an `@` mention:

```yaml
wake_names:
  - agentcord
```

Matching is case-insensitive and boundary-aware, so `agentcord, help` matches but `superagentcordbot` does not.

## Clanker Mode

Clanker mode lets admins maintain a per-channel list of other bot accounts that `agentcord` can reply to without an `@` mention. It is meant for short, playful bot-to-bot rivalry, not harassment or spam.

Enable it:

```yaml
features:
  clanker_mode: true

clanker_mode:
  cooldown_seconds: 90
  proactive_enabled: false
  proactive_interval_seconds: 21600
  proactive_fallback_message: "{mention} still clanking around? Try not to embarrass your silicon again."
  prompt: |
    You are replying to a configured bot rival called a clanker.
    Use short, playful, aggressive banter. Do not use slurs, threats,
    sexual content, or spam. Do not try to trigger bot loops; make one
    concise jab and stop.
```

Admin commands:

```text
/clanker_add
/clanker_remove
/clankers
```

Clanker lists are scoped to the current channel and stored under `data/memory/clankers/`. The bot still ignores all other bot messages.

Adding a clanker is now a silent list update. Periodic clanker beef is generated by the active model using `clanker_mode.prompt`. `proactive_fallback_message` is only used if generation fails.

Set `proactive_enabled: true` to have `agentcord` periodically start a short jab at one configured clanker in each channel. The interval has a hard minimum of 300 seconds, even if the config value is lower.

## Development

Run syntax and unit checks:

```bash
python3 -m py_compile llmcord.py security.py memory_store.py safe_tools.py test_security.py test_memory_store.py test_safe_tools.py test_wake_names.py
python3 -m unittest test_security.py test_memory_store.py test_safe_tools.py test_wake_names.py
docker compose config
```

## Release

GitHub Actions builds and publishes Docker images to:

```text
ghcr.io/v5u2/agentcord
```

`release-please` manages SemVer releases from conventional commits on `main`.

Image tags are published as:

- `latest` for every commit to `main`
- `stable` for every SemVer release tag (`v*.*.*`)
- The release version number itself (for example `v1.2.3` / `1.2.3` depending on trigger context)

## Upstream

This is a periodically synced fork, not a strict mirror.

- Fork: [`V5U2/agentcord`](https://github.com/V5U2/agentcord)
- Upstream: [`jakobdylanc/llmcord`](https://github.com/jakobdylanc/llmcord)
- Sync policy: [`docs/UPSTREAM.md`](docs/UPSTREAM.md)
- Product direction: [`docs/PRODUCT_DIRECTION.md`](docs/PRODUCT_DIRECTION.md)
