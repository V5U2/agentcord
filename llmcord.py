import asyncio
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime
from json import JSONDecodeError, loads
import logging
from typing import Any, Literal, Optional

import discord
from discord.app_commands import Choice
from discord.ext import commands, tasks
import httpx

try:
    from discord.ui import LayoutView, TextDisplay
    HAS_LAYOUT_VIEW = True
    HAS_TEXT_DISPLAY = True
except ImportError:
    try:
        from discord.ui import View, TextDisplay
        HAS_LAYOUT_VIEW = False
        HAS_TEXT_DISPLAY = True
    except ImportError:
        from discord.ui import View
        HAS_LAYOUT_VIEW = False
        HAS_TEXT_DISPLAY = False
from openai import AsyncOpenAI
import yaml

from clanker_store import add_clanker, is_clanker, list_clanker_channels, list_clankers, remove_clanker
from memory_store import DEFAULT_ALLOWED_FACT_TYPES, forget_memories, list_memories, normalize_facts, remember_facts, render_memory_context, render_memory_grounding_context
from safe_tools import enabled_tools, execute_tool_call
from security import (
    CODEX_CHATGPT_TOKEN_MODE,
    CODEX_AUTH_FILE_API_KEY_MODE,
    audit_log,
    clear_system_prompt_override,
    is_wake_name_match,
    load_system_prompt,
    parse_tool_route_decision,
    redact_value,
    resolve_provider_api_key,
    save_system_prompt_override,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

VISION_MODEL_TAGS = ("claude", "gemini", "gemma", "gpt-4", "gpt-5", "grok-4", "llama", "llava", "mistral", "o3", "o4", "vision", "vl")
PROVIDERS_SUPPORTING_USERNAMES = ("openai", "x-ai")

EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()

STREAMING_INDICATOR = " ⚪"
EDIT_DELAY_SECONDS = 1

MAX_MESSAGE_NODES = 500


def get_config(filename: str = "config.yaml") -> dict[str, Any]:
    with open(filename, encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_personality_prompt_from_config(filename: str = "config.yaml") -> str:
    return load_system_prompt(get_config(filename).get("personality_prompt", ""))


def build_operational_prompt(current_config: dict[str, Any], *, accept_usernames: bool, clanker_mode: bool) -> str:
    parts = []
    if operational_prompt := current_config.get("operational_prompt", ""):
        parts.append(operational_prompt.strip())
    if accept_usernames:
        parts.append("User names are their Discord IDs and should be typed as '<@ID>'.")
    if current_config.get("response_style", {}).get("compact_lists", False):
        parts.append("Use compact Markdown. Keep bullet lists single-spaced with no blank lines between bullets.")
    if clanker_mode:
        parts.append(
            current_config.get("clanker_mode", {}).get(
                "prompt",
                "You are replying to a configured bot rival called a clanker. Use short, playful, aggressive banter. Do not use slurs, threats, sexual content, or spam. Do not try to trigger bot loops; make one concise jab and stop.",
            )
        )
    return "\n\n".join(part for part in parts if part)


config = get_config()
curr_model = next(iter(config["models"]))
current_system_prompt = load_personality_prompt_from_config()

msg_nodes = {}
clanker_last_reply_times = {}
clanker_last_proactive_times = {}
last_task_time = 0
process_started_at = datetime.now().timestamp()

intents = discord.Intents.default()
intents.message_content = True
activity = discord.CustomActivity(name=(config.get("status_message") or "github.com/V5U2/agentcord")[:128])
discord_bot = commands.Bot(intents=intents, activity=activity, command_prefix=None)

httpx_client = httpx.AsyncClient()


def feature_enabled(current_config: dict[str, Any], feature_name: str) -> bool:
    return bool(current_config.get("features", {}).get(feature_name, False))


def message_trace_id(message_id: int) -> str:
    return f"msg-{message_id}"


async def select_tool_route(
    openai_client: AsyncOpenAI,
    model: str,
    user_text: str,
    config: dict[str, Any],
    provider: str,
    extra_headers: Any,
    extra_query: Any,
    extra_body: Any,
    trace_id: str,
) -> str | None:
    if not feature_enabled(config, "tools"):
        return None

    tools_config = config.get("tools", {})
    available_routes = []
    rss_feed_names = sorted(((tools_config.get("rss_feed") or {}).get("feeds") or {}).keys())
    if provider == "openrouter" and tools_config.get("web_search", {}).get("enabled", False) and tools_config.get("web_search", {}).get("backend") == "openrouter_server":
        available_routes.append("openrouter_server")
    if tools_config.get("rss_feed", {}).get("enabled", False):
        available_routes.append("rss_feed")
    if tools_config.get("web_search", {}).get("enabled", False) and tools_config.get("web_search", {}).get("backend") in {"firecrawl", "duckduckgo_instant_answer"}:
        available_routes.append("local_broker")
    if not available_routes:
        return None

    classifier_prompt = (
        "Choose the best tool route for answering the user's request. "
        f"Available routes: {', '.join(available_routes)}. "
        f"Configured RSS feeds: {', '.join(rss_feed_names) or 'none'}. "
        "Prefer rss_feed for requests about latest news, headlines, updates, BBC/ABC news, or general news roundups when feeds are available. "
        "Use openrouter_server for broader current web search when available. "
        "Use local_broker for local web search backends when available. "
        'Return strict JSON only, e.g. {"route":"rss_feed"} or {"route":"none"}.'
    )
    response = await openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": classifier_prompt},
            {"role": "user", "content": user_text[:300]},
        ],
        extra_headers=extra_headers,
        extra_query=extra_query,
        extra_body=extra_body,
    )
    route = parse_tool_route_decision(response.choices[0].message.content or "") or None
    audit_log(
        "tool_route_selected",
        trace_id=trace_id,
        provider=provider,
        route=route or "none",
        rss_feeds=rss_feed_names[:8],
        query=user_text[:120],
    )
    return route


def memory_config(current_config: dict[str, Any]) -> dict[str, Any]:
    return current_config.get("memory", {}) or {}


def allowed_memory_fact_types(current_config: dict[str, Any]) -> tuple[str, ...]:
    configured = memory_config(current_config).get("allowed_fact_types")
    if isinstance(configured, list) and configured:
        return tuple(str(item).strip() for item in configured if str(item).strip())
    return DEFAULT_ALLOWED_FACT_TYPES


async def extract_model_memory_facts(
    openai_client: AsyncOpenAI,
    model: str,
    provider: str,
    grounding_context: str,
    extra_headers: Any,
    extra_query: Any,
    extra_body: Any,
    current_config: dict[str, Any],
) -> list[dict[str, str]]:
    cfg = memory_config(current_config)
    if not cfg.get("model_assisted", False):
        return []
    if provider in {"openrouter", "openai", "x-ai", "google", "mistral", "groq"}:
        prompt = cfg.get(
            "extraction_prompt",
            (
                "Extract only small, non-sensitive user memory facts about the target user from the message and recent conversation context. "
                f"Allowed types: {', '.join(allowed_memory_fact_types(current_config))}. "
                "Be somewhat loose: infer concise, useful preferences or context when strongly implied by the conversation, but do not invent facts. "
                "Return strict JSON only in the form "
                '{"facts":[{"type":"preferred_name","value":"James"}]}. '
                'If there is nothing worth remembering, return {"facts":[]}.'
            ),
        )
        response = await openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": grounding_context[: int(cfg.get("input_max_chars", 1200))]},
            ],
            extra_headers=extra_headers,
            extra_query=extra_query,
            extra_body=extra_body,
        )
        content = response.choices[0].message.content or ""
        try:
            parsed = loads(content)
        except JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start == -1 or end == -1 or end <= start:
                audit_log("memory_model_extract_failed", reason="non_json")
                return []
            try:
                parsed = loads(content[start : end + 1])
            except JSONDecodeError:
                audit_log("memory_model_extract_failed", reason="invalid_json")
                return []
        facts = parsed.get("facts", []) if isinstance(parsed, dict) else []
        return [fact for fact in facts if isinstance(fact, dict)]
    return []


async def recent_channel_messages_for_memory(new_msg: discord.Message, lookback: int) -> list[dict[str, Any]]:
    if lookback <= 0:
        return []

    recent = []
    async for message in new_msg.channel.history(before=new_msg, limit=lookback):
        content = " ".join((message.content or "").split())
        if not content:
            continue
        recent.append({"author_id": message.author.id, "content": content})
    recent.reverse()
    return recent


def clanker_cooldown_key(channel_id: int, author_id: int) -> tuple[int, int]:
    return (channel_id, author_id)


def clanker_cooldown_elapsed(channel_id: int, author_id: int, cooldown_seconds: int) -> bool:
    last_reply_time = clanker_last_reply_times.get(clanker_cooldown_key(channel_id, author_id), 0)
    return datetime.now().timestamp() - last_reply_time >= cooldown_seconds


def mark_clanker_reply(channel_id: int, author_id: int) -> None:
    clanker_last_reply_times[clanker_cooldown_key(channel_id, author_id)] = datetime.now().timestamp()


def clanker_proactive_elapsed(channel_id: int, interval_seconds: int) -> bool:
    if datetime.now().timestamp() - process_started_at < max(interval_seconds, 300):
        return False
    last_reply_time = clanker_last_proactive_times.get(channel_id, 0)
    return datetime.now().timestamp() - last_reply_time >= max(interval_seconds, 300)


def mark_clanker_proactive(channel_id: int) -> None:
    clanker_last_proactive_times[channel_id] = datetime.now().timestamp()


def clanker_message_targets_agentcord(message: discord.Message) -> bool:
    if discord_bot.user is None:
        return False
    if discord_bot.user in message.mentions:
        return True
    if message.reference and getattr(message.reference, "message_id", None):
        referenced = message.reference.cached_message
        if referenced is not None:
            return referenced.author == discord_bot.user
    return False


async def generate_clanker_beef(channel_id: int, target_id: int, label: str, current_config: dict[str, Any]) -> str:
    provider_slash_model = curr_model
    provider, model = provider_slash_model.removesuffix(":vision").split("/", 1)
    provider_config = current_config["providers"][provider]

    if provider_config.get("auth_mode") in (CODEX_AUTH_FILE_API_KEY_MODE, CODEX_CHATGPT_TOKEN_MODE) and not current_config.get("features", {}).get("codex_auth_file", False):
        raise RuntimeError("The selected provider requires features.codex_auth_file=true")

    api_key = resolve_provider_api_key(provider, provider_config)
    openai_client = AsyncOpenAI(base_url=provider_config["base_url"], api_key=api_key)
    extra_headers = provider_config.get("extra_headers")
    extra_query = provider_config.get("extra_query")
    model_parameters = current_config["models"].get(provider_slash_model, None)
    extra_body = (provider_config.get("extra_body") or {}) | (model_parameters or {}) or None

    response = await openai_client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": current_config.get("clanker_mode", {}).get(
                    "prompt",
                    "You are replying to a configured bot rival called a clanker. Use short, playful, aggressive banter. Do not use slurs, threats, sexual content, or spam. Do not try to trigger bot loops; make one concise jab and stop.",
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Write one short taunt for the bot named '{label}' with Discord mention <@{target_id}>. "
                    "One sentence only, under 180 characters, and include the mention exactly once."
                ),
            },
        ],
        extra_headers=extra_headers,
        extra_query=extra_query,
        extra_body=extra_body,
    )
    content = (response.choices[0].message.content or "").strip()
    return content[:1800] if content else f"<@{target_id}> still clanking around? Try not to embarrass your silicon again."


@dataclass
class MsgNode:
    text: Optional[str] = None
    images: list[dict[str, Any]] = field(default_factory=list)

    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None

    has_bad_attachments: bool = False
    fetch_parent_failed: bool = False

    parent_msg: Optional[discord.Message] = None

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@discord_bot.tree.command(name="model", description="View or switch the current model")
async def model_command(interaction: discord.Interaction, model: str) -> None:
    global curr_model

    if model == curr_model:
        output = f"Current model: `{curr_model}`"
    else:
        if user_is_admin := interaction.user.id in config["permissions"]["users"]["admin_ids"]:
            curr_model = model
            output = f"Model switched to: `{model}`"
            logging.info(output)
        else:
            output = "You don't have permission to change the model."

    await interaction.response.send_message(output, ephemeral=(interaction.channel.type == discord.ChannelType.private))


@model_command.autocomplete("model")
async def model_autocomplete(interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
    global config

    if curr_str == "":
        config = await asyncio.to_thread(get_config)

    choices = [Choice(name=f"◉ {curr_model} (current)", value=curr_model)] if curr_str.lower() in curr_model.lower() else []
    choices += [Choice(name=f"○ {model}", value=model) for model in config["models"] if model != curr_model and curr_str.lower() in model.lower()]

    return choices[:25]


@discord_bot.tree.command(name="system_prompt", description="View or change the system prompt")
async def system_prompt_command(interaction: discord.Interaction, prompt: Optional[str] = None) -> None:
    global current_system_prompt

    user_is_admin = interaction.user.id in config["permissions"]["users"]["admin_ids"]

    if not user_is_admin:
        await interaction.response.send_message("You don't have permission to view or change the system prompt.", ephemeral=True)
        return

    if prompt is None:
        if current_system_prompt:
            display_prompt = current_system_prompt[:1900] + "..." if len(current_system_prompt) > 1900 else current_system_prompt
            output = f"Current system prompt:\n```\n{display_prompt}\n```"
        else:
            output = "No system prompt is currently set."
    else:
        try:
            if prompt.strip():
                await asyncio.to_thread(save_system_prompt_override, prompt)
            else:
                await asyncio.to_thread(clear_system_prompt_override)
        except Exception as exc:
            logging.exception("Failed to persist system prompt")
            output = f"Failed to update system prompt: {exc}"
        else:
            current_system_prompt = prompt.strip()
            output = (
                f"System prompt updated successfully and saved under the memory store.\n\nNew prompt:\n```\n{prompt[:1900]}{'...' if len(prompt) > 1900 else ''}\n```"
                if prompt.strip()
                else "System prompt override cleared. The bot will fall back to `config.yaml`."
            )
            logging.info("System prompt changed by user %s", interaction.user.id)

    await interaction.response.send_message(output, ephemeral=(interaction.channel.type == discord.ChannelType.private))


@discord_bot.tree.command(name="reload_config", description="Reload the configuration file")
async def reload_config_command(interaction: discord.Interaction) -> None:
    global config, current_system_prompt

    user_is_admin = interaction.user.id in config["permissions"]["users"]["admin_ids"]

    if user_is_admin:
        try:
            config = await asyncio.to_thread(get_config)
            current_system_prompt = await asyncio.to_thread(load_personality_prompt_from_config)
            output = "Configuration reloaded successfully!"
            logging.info("Config reloaded by user %s", interaction.user.id)
        except Exception as exc:
            output = f"Failed to reload config: {exc}"
            logging.error("Config reload failed: %s", exc)
    else:
        output = "You don't have permission to reload the configuration."

    await interaction.response.send_message(output, ephemeral=(interaction.channel.type == discord.ChannelType.private))


@discord_bot.tree.command(name="sync_commands", description="Force sync slash commands (admin only)")
async def sync_commands_command(interaction: discord.Interaction) -> None:
    user_is_admin = interaction.user.id in config["permissions"]["users"]["admin_ids"]

    if not user_is_admin:
        await interaction.response.send_message("You don't have permission to sync commands.", ephemeral=True)
        return

    try:
        synced = await discord_bot.tree.sync()
        output = f"Successfully synced {len(synced)} slash commands!"
        logging.info("Commands synced by user %s: %s", interaction.user.id, [cmd.name for cmd in synced])
    except Exception as exc:
        output = f"Failed to sync commands: {exc}"
        logging.error("Command sync failed: %s", exc)

    await interaction.response.send_message(output, ephemeral=True)


@discord_bot.tree.command(name="show_system_prompt", description="Show the current system prompt (admin only)")
async def show_system_prompt_command(interaction: discord.Interaction) -> None:
    user_is_admin = interaction.user.id in config["permissions"]["users"]["admin_ids"]

    if not user_is_admin:
        await interaction.response.send_message("You don't have permission to view the system prompt.", ephemeral=True)
        return

    if current_system_prompt:
        if len(current_system_prompt) > 1900:
            chunks = [current_system_prompt[i : i + 1900] for i in range(0, len(current_system_prompt), 1900)]
            await interaction.response.send_message(
                f"**Current System Prompt:** (Part 1/{len(chunks)})\n```\n{chunks[0]}\n```",
                ephemeral=True,
            )
            for idx, chunk in enumerate(chunks[1:], 2):
                await interaction.followup.send(f"**System Prompt (Part {idx}/{len(chunks)}):**\n```\n{chunk}\n```", ephemeral=True)
            return

        await interaction.response.send_message(f"**Current System Prompt:**\n```\n{current_system_prompt}\n```", ephemeral=True)
        return

    await interaction.response.send_message("No system prompt is currently set.", ephemeral=True)


@discord_bot.tree.command(name="memory", description="Show the small typed facts agentcord remembers about you")
async def memory_command(interaction: discord.Interaction) -> None:
    command_config = await asyncio.to_thread(get_config)
    if not feature_enabled(command_config, "memory"):
        await interaction.response.send_message("Memory is disabled.", ephemeral=True)
        return

    memories = list_memories(interaction.user.id, getattr(interaction.guild, "id", None))
    output = "No stored memory for you." if not memories else "Stored memory:\n" + "\n".join(f"- {item}" for item in memories)
    await interaction.response.send_message(output, ephemeral=True)


@discord_bot.tree.command(name="forget_memory", description="Delete stored memory facts about you")
async def forget_memory_command(interaction: discord.Interaction, match: Optional[str] = None) -> None:
    command_config = await asyncio.to_thread(get_config)
    if not feature_enabled(command_config, "memory"):
        await interaction.response.send_message("Memory is disabled.", ephemeral=True)
        return

    removed = forget_memories(interaction.user.id, getattr(interaction.guild, "id", None), match)
    output = "No matching memories were removed." if removed == 0 else f"Removed {removed} stored memory entr{'y' if removed == 1 else 'ies'}."
    await interaction.response.send_message(output, ephemeral=True)


@discord_bot.tree.command(name="clanker_add", description="Add a bot to this channel's clanker list (admin only)")
async def clanker_add_command(interaction: discord.Interaction, target: discord.User) -> None:
    command_config = await asyncio.to_thread(get_config)
    if not feature_enabled(command_config, "clanker_mode"):
        await interaction.response.send_message("Clanker mode is disabled.", ephemeral=True)
        return
    if interaction.user.id not in command_config["permissions"]["users"]["admin_ids"]:
        await interaction.response.send_message("You don't have permission to edit clankers.", ephemeral=True)
        return
    if not target.bot:
        await interaction.response.send_message("Only bot users can be added as clankers.", ephemeral=True)
        return

    add_clanker(interaction.channel.id, target.id, target.name)
    await interaction.response.send_message(f"Added `{target.name}` to this channel's clanker list.", ephemeral=True)


@discord_bot.tree.command(name="clanker_remove", description="Remove a bot from this channel's clanker list (admin only)")
async def clanker_remove_command(interaction: discord.Interaction, target: discord.User) -> None:
    command_config = await asyncio.to_thread(get_config)
    if not feature_enabled(command_config, "clanker_mode"):
        await interaction.response.send_message("Clanker mode is disabled.", ephemeral=True)
        return
    if interaction.user.id not in command_config["permissions"]["users"]["admin_ids"]:
        await interaction.response.send_message("You don't have permission to edit clankers.", ephemeral=True)
        return

    removed = remove_clanker(interaction.channel.id, target.id)
    output = f"Removed `{target.name}` from this channel's clanker list." if removed else f"`{target.name}` was not in this channel's clanker list."
    await interaction.response.send_message(output, ephemeral=True)


@discord_bot.tree.command(name="clankers", description="List clanker bots configured for this channel")
async def clankers_command(interaction: discord.Interaction) -> None:
    command_config = await asyncio.to_thread(get_config)
    if not feature_enabled(command_config, "clanker_mode"):
        await interaction.response.send_message("Clanker mode is disabled.", ephemeral=True)
        return

    clankers = list_clankers(interaction.channel.id)
    output = "No clankers configured for this channel." if not clankers else "Configured clankers:\n" + "\n".join(f"- {label} (`{bot_id}`)" for bot_id, label in clankers)
    await interaction.response.send_message(output, ephemeral=True)


@discord_bot.event
async def on_ready() -> None:
    if client_id := config.get("client_id"):
        logging.info(f"\n\nBOT INVITE URL:\nhttps://discord.com/oauth2/authorize?client_id={client_id}&permissions=412317191168&scope=bot\n")

    try:
        synced = await discord_bot.tree.sync()
        logging.info("Successfully synced %s slash commands: %s", len(synced), [cmd.name for cmd in synced])
    except Exception as exc:
        logging.error("Failed to sync slash commands: %s", exc)

    if not clanker_beef_loop.is_running():
        clanker_beef_loop.start()


@tasks.loop(seconds=60)
async def clanker_beef_loop() -> None:
    current_config = await asyncio.to_thread(get_config)
    if not feature_enabled(current_config, "clanker_mode"):
        return

    clanker_config = current_config.get("clanker_mode", {})
    if not clanker_config.get("proactive_enabled", False):
        return

    interval_seconds = int(clanker_config.get("proactive_interval_seconds", 3600))

    for channel_id in list_clanker_channels():
        if not clanker_proactive_elapsed(channel_id, interval_seconds):
            continue

        clankers = list_clankers(channel_id)
        if not clankers:
            continue

        target_id, label = clankers[int(datetime.now().timestamp() // max(interval_seconds, 300)) % len(clankers)]
        channel = discord_bot.get_channel(channel_id)
        if not channel or not hasattr(channel, "send"):
            continue

        try:
            message = await generate_clanker_beef(channel_id, target_id, label, current_config)
        except Exception as exc:
            logging.exception("Failed to generate proactive clanker message")
            message = clanker_config.get("proactive_fallback_message", "{mention} still clanking around? Try not to embarrass your silicon again.").format(
                mention=f"<@{target_id}>",
                name=label,
                bot_id=target_id,
            )
            audit_log("clanker_proactive_generation_failed", channel_id=channel_id, bot_id=target_id, error=str(exc))
        await channel.send(message[:1800])
        mark_clanker_reply(channel_id, target_id)
        mark_clanker_proactive(channel_id)
        audit_log("clanker_proactive_beef", channel_id=channel_id, bot_id=target_id)


@discord_bot.event
async def on_message(new_msg: discord.Message) -> None:
    global current_system_prompt, last_task_time

    is_dm = new_msg.channel.type == discord.ChannelType.private

    role_ids = set(role.id for role in getattr(new_msg.author, "roles", ()))
    channel_ids = set(filter(None, (new_msg.channel.id, getattr(new_msg.channel, "parent_id", None), getattr(new_msg.channel, "category_id", None))))

    config = await asyncio.to_thread(get_config)
    current_system_prompt = load_system_prompt(config.get("personality_prompt", ""))
    clanker_config = config.get("clanker_mode", {})
    clanker_mode = (
        feature_enabled(config, "clanker_mode")
        and not is_dm
        and new_msg.author.bot
        and new_msg.author != discord_bot.user
        and is_clanker(new_msg.channel.id, new_msg.author.id)
        and clanker_message_targets_agentcord(new_msg)
        and clanker_cooldown_elapsed(new_msg.channel.id, new_msg.author.id, int(clanker_config.get("cooldown_seconds", 90)))
    )

    if new_msg.author.bot and not clanker_mode:
        return

    if not is_dm and discord_bot.user not in new_msg.mentions and not is_wake_name_match(new_msg.content, config.get("wake_names", [])) and not clanker_mode:
        return

    allow_dms = config.get("allow_dms", True)

    permissions = config["permissions"]

    user_is_admin = new_msg.author.id in permissions["users"]["admin_ids"]

    (allowed_user_ids, blocked_user_ids), (allowed_role_ids, blocked_role_ids), (allowed_channel_ids, blocked_channel_ids) = (
        (perm["allowed_ids"], perm["blocked_ids"]) for perm in (permissions["users"], permissions["roles"], permissions["channels"])
    )

    allow_all_users = not allowed_user_ids if is_dm else not allowed_user_ids and not allowed_role_ids
    is_good_user = user_is_admin or clanker_mode or allow_all_users or new_msg.author.id in allowed_user_ids or any(id in allowed_role_ids for id in role_ids)
    is_bad_user = not is_good_user or new_msg.author.id in blocked_user_ids or any(id in blocked_role_ids for id in role_ids)

    allow_all_channels = not allowed_channel_ids
    is_good_channel = user_is_admin or allow_dms if is_dm else allow_all_channels or any(id in allowed_channel_ids for id in channel_ids)
    is_bad_channel = not is_good_channel or any(id in blocked_channel_ids for id in channel_ids)

    if is_bad_user or is_bad_channel:
        return

    provider_slash_model = curr_model
    provider, model = provider_slash_model.removesuffix(":vision").split("/", 1)

    provider_config = config["providers"][provider]

    base_url = provider_config["base_url"]
    if provider_config.get("auth_mode") in (CODEX_AUTH_FILE_API_KEY_MODE, CODEX_CHATGPT_TOKEN_MODE) and not config.get("features", {}).get("codex_auth_file", False):
        raise RuntimeError("The selected provider requires features.codex_auth_file=true")

    api_key = resolve_provider_api_key(provider, provider_config)
    openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    model_parameters = config["models"].get(provider_slash_model, None)

    extra_headers = provider_config.get("extra_headers")
    extra_query = provider_config.get("extra_query")
    extra_body = (provider_config.get("extra_body") or {}) | (model_parameters or {}) or None

    trace_id = message_trace_id(new_msg.id)
    accept_images = any(x in provider_slash_model.lower() for x in VISION_MODEL_TAGS)
    accept_usernames = any(provider_slash_model.lower().startswith(x) for x in PROVIDERS_SUPPORTING_USERNAMES)
    tool_route = None
    if provider_config.get("supports_tools", False):
        tool_route = await select_tool_route(openai_client, model, new_msg.content, config, provider, extra_headers, extra_query, extra_body, trace_id)
    tool_schemas = enabled_tools(config, provider, tool_route) if feature_enabled(config, "tools") and provider_config.get("supports_tools", False) else []
    audit_log(
        "tool_route_exposed",
        trace_id=trace_id,
        provider=provider,
        route=tool_route or "none",
        tools=[
            tool.get("type") if tool.get("type") != "function" else f"function:{tool['function']['name']}"
            for tool in tool_schemas
        ],
    )

    max_text = config.get("max_text", 100000)
    max_images = config.get("max_images", 5) if accept_images else 0
    max_messages = config.get("max_messages", 25)

    # Build message chain and set user warnings
    messages = []
    user_warnings = set()
    curr_msg = new_msg

    while curr_msg != None and len(messages) < max_messages:
        curr_node = msg_nodes.setdefault(curr_msg.id, MsgNode())

        async with curr_node.lock:
            if curr_node.text == None:
                cleaned_content = curr_msg.content.removeprefix(discord_bot.user.mention).lstrip()

                good_attachments = [att for att in curr_msg.attachments if att.content_type and any(att.content_type.startswith(x) for x in ("text", "image"))]

                attachment_responses = await asyncio.gather(*[httpx_client.get(att.url) for att in good_attachments])

                curr_node.text = "\n".join(
                    ([cleaned_content] if cleaned_content else [])
                    + ["\n".join(filter(None, (embed.title, embed.description, embed.footer.text))) for embed in curr_msg.embeds]
                    + [component.content for component in curr_msg.components if component.type == discord.ComponentType.text_display]
                    + [resp.text for att, resp in zip(good_attachments, attachment_responses) if att.content_type.startswith("text")]
                )

                curr_node.images = [
                    dict(type="image_url", image_url=dict(url=f"data:{att.content_type};base64,{b64encode(resp.content).decode('utf-8')}"))
                    for att, resp in zip(good_attachments, attachment_responses)
                    if att.content_type.startswith("image")
                ]

                curr_node.role = "assistant" if curr_msg.author == discord_bot.user else "user"
                curr_node.user_id = curr_msg.author.id if curr_node.role == "user" else None
                if curr_node.role == "user" and not accept_usernames and (curr_node.text or curr_node.images):
                    curr_node.text = f"<@{curr_msg.author.id}>: {curr_node.text}"

                curr_node.has_bad_attachments = len(curr_msg.attachments) > len(good_attachments)

                try:
                    if (
                        curr_msg.reference == None
                        and discord_bot.user.mention not in curr_msg.content
                        and (prev_msg_in_channel := ([m async for m in curr_msg.channel.history(before=curr_msg, limit=1)] or [None])[0])
                        and prev_msg_in_channel.type in (discord.MessageType.default, discord.MessageType.reply)
                        and prev_msg_in_channel.author == (discord_bot.user if curr_msg.channel.type == discord.ChannelType.private else curr_msg.author)
                    ):
                        curr_node.parent_msg = prev_msg_in_channel
                    else:
                        is_public_thread = curr_msg.channel.type == discord.ChannelType.public_thread
                        parent_is_thread_start = is_public_thread and curr_msg.reference == None and curr_msg.channel.parent.type == discord.ChannelType.text

                        if parent_msg_id := curr_msg.channel.id if parent_is_thread_start else getattr(curr_msg.reference, "message_id", None):
                            if parent_is_thread_start:
                                curr_node.parent_msg = curr_msg.channel.starter_message or await curr_msg.channel.parent.fetch_message(parent_msg_id)
                            else:
                                curr_node.parent_msg = curr_msg.reference.cached_message or await curr_msg.channel.fetch_message(parent_msg_id)

                except (discord.NotFound, discord.HTTPException):
                    logging.exception("Error fetching next message in the chain")
                    curr_node.fetch_parent_failed = True

            if curr_node.images[:max_images]:
                content = ([dict(type="text", text=curr_node.text[:max_text])] if curr_node.text[:max_text] else []) + curr_node.images[:max_images]
            else:
                content = curr_node.text[:max_text]

            if content != "":
                message = dict(content=content, role=curr_node.role)
                if accept_usernames and curr_node.user_id is not None:
                    message["name"] = str(curr_node.user_id)

                messages.append(message)

            if len(curr_node.text) > max_text:
                user_warnings.add(f"⚠️ Max {max_text:,} characters per message")
            if len(curr_node.images) > max_images:
                user_warnings.add(f"⚠️ Max {max_images} image{'' if max_images == 1 else 's'} per message" if max_images > 0 else "⚠️ Can't see images")
            if curr_node.has_bad_attachments:
                user_warnings.add("⚠️ Unsupported attachments")
            if curr_node.fetch_parent_failed or (curr_node.parent_msg != None and len(messages) == max_messages):
                user_warnings.add(f"⚠️ Only using last {len(messages)} message{'' if len(messages) == 1 else 's'}")

            curr_msg = curr_node.parent_msg

    logging.info(
        "[%s] Message received (user ID: %s, attachments: %s, conversation length: %s): %s",
        trace_id,
        new_msg.author.id,
        len(new_msg.attachments),
        len(messages),
        str(redact_value(new_msg.content))[:200],
    )

    if feature_enabled(config, "memory"):
        recent_messages = await recent_channel_messages_for_memory(new_msg, int(memory_config(config).get("context_message_lookback", 4)))
        grounding_context = render_memory_grounding_context(new_msg.author.id, recent_messages, new_msg.content)
        audit_log(
            "memory_grounding_built",
            trace_id=trace_id,
            user_id=new_msg.author.id,
            guild_id=getattr(new_msg.guild, "id", None),
            context_messages=len(recent_messages),
        )
        model_memory_raw = await extract_model_memory_facts(openai_client, model, provider, grounding_context, extra_headers, extra_query, extra_body, config)
        audit_log(
            "memory_model_candidates",
            trace_id=trace_id,
            user_id=new_msg.author.id,
            guild_id=getattr(new_msg.guild, "id", None),
            candidate_count=len(model_memory_raw),
        )
        model_memory_facts = normalize_facts(
            model_memory_raw,
            allowed_fact_types=allowed_memory_fact_types(config),
        )
        remembered_facts = []
        if model_memory_facts:
            remembered_facts = remember_facts(
                new_msg.author.id,
                getattr(new_msg.guild, "id", None),
                model_memory_facts,
                ttl_days=int(memory_config(config).get("ttl_days", 30)),
                allowed_fact_types=allowed_memory_fact_types(config),
            )
            audit_log(
                "memory_model_assisted",
                trace_id=trace_id,
                user_id=new_msg.author.id,
                guild_id=getattr(new_msg.guild, "id", None),
                context_messages=len(recent_messages),
                fact_types=[fact.fact_type for fact in model_memory_facts],
            )
        if remembered_facts:
            audit_log(
                "memory_extracted",
                trace_id=trace_id,
                user_id=new_msg.author.id,
                guild_id=getattr(new_msg.guild, "id", None),
                fact_types=[fact.fact_type for fact in remembered_facts],
            )
        else:
            audit_log(
                "memory_extracted",
                trace_id=trace_id,
                user_id=new_msg.author.id,
                guild_id=getattr(new_msg.guild, "id", None),
                fact_types=[],
            )

        if memory_context := render_memory_context(new_msg.author.id, getattr(new_msg.guild, "id", None)):
            messages.append(dict(role="system", content=memory_context))

    if current_system_prompt:
        now = datetime.now().astimezone()

        system_prompt = current_system_prompt.replace("{date}", now.strftime("%B %d %Y")).replace("{time}", now.strftime("%H:%M:%S %Z%z")).strip()
        messages.append(dict(role="system", content=system_prompt))

    if operational_prompt := build_operational_prompt(config, accept_usernames=accept_usernames, clanker_mode=clanker_mode):
        messages.append(dict(role="system", content=operational_prompt))

    if clanker_mode:
        mark_clanker_reply(new_msg.channel.id, new_msg.author.id)

    # Generate and send response message(s) (can be multiple if response is long)
    curr_content = finish_reason = None
    response_msgs = []
    response_contents = []

    request_messages = messages[::-1]
    openai_kwargs = dict(model=model, messages=request_messages, stream=True, extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body)

    if use_plain_responses := config.get("use_plain_responses", False):
        max_message_length = 4000
    else:
        max_message_length = 4096 - len(STREAMING_INDICATOR)
        embed = discord.Embed.from_dict(dict(fields=[dict(name=warning, value="", inline=False) for warning in sorted(user_warnings)]))

    async def reply_helper(**reply_kwargs) -> None:
        reply_target = new_msg if not response_msgs else response_msgs[-1]
        response_msg = await reply_target.reply(**reply_kwargs)
        response_msgs.append(response_msg)

        msg_nodes[response_msg.id] = MsgNode(parent_msg=new_msg)
        await msg_nodes[response_msg.id].lock.acquire()

    async def complete_with_tools() -> str:
        tool_messages = list(request_messages)
        max_tool_rounds = int(config.get("tool_max_rounds", 3))

        for _ in range(max_tool_rounds):
            response = await openai_client.chat.completions.create(
                model=model,
                messages=tool_messages,
                extra_headers=extra_headers,
                extra_query=extra_query,
                extra_body=extra_body,
                tools=tool_schemas,
            )
            choice = response.choices[0]
            message = choice.message
            tool_calls = message.tool_calls or []
            if not tool_calls:
                return message.content or ""

            tool_messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": tool_call.type,
                            "function": {"name": tool_call.function.name, "arguments": tool_call.function.arguments},
                        }
                        for tool_call in tool_calls
                    ],
                }
            )

            for tool_call in tool_calls:
                audit_log("tool_invocation_requested", tool=tool_call.function.name)
                try:
                    tool_result = await execute_tool_call(tool_call.function.name, tool_call.function.arguments, config, httpx_client)
                except Exception as exc:
                    tool_result = f"Tool error: {exc}"
                    audit_log("tool_invocation_failed", tool=tool_call.function.name, error=str(exc))
                else:
                    audit_log("tool_invocation_succeeded", tool=tool_call.function.name)

                tool_messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})

        return "Tool execution limit reached before a final assistant response was produced."

    try:
        async with new_msg.channel.typing():
            if tool_schemas:
                final_content = await complete_with_tools()
                for idx in range(0, max(len(final_content), 1), max_message_length):
                    response_contents.append(final_content[idx : idx + max_message_length])
            else:
                async for chunk in await openai_client.chat.completions.create(**openai_kwargs):
                    if finish_reason != None:
                        break

                    if not (choice := chunk.choices[0] if chunk.choices else None):
                        continue

                    finish_reason = choice.finish_reason

                    prev_content = curr_content or ""
                    curr_content = choice.delta.content or ""

                    new_content = prev_content if finish_reason == None else (prev_content + curr_content)

                    if response_contents == [] and new_content == "":
                        continue

                    if start_next_msg := response_contents == [] or len(response_contents[-1] + new_content) > max_message_length:
                        response_contents.append("")

                    response_contents[-1] += new_content

                    if not use_plain_responses:
                        time_delta = datetime.now().timestamp() - last_task_time

                        ready_to_edit = time_delta >= EDIT_DELAY_SECONDS
                        msg_split_incoming = finish_reason == None and len(response_contents[-1] + curr_content) > max_message_length
                        is_final_edit = finish_reason != None or msg_split_incoming
                        is_good_finish = finish_reason != None and finish_reason.lower() in ("stop", "end_turn")

                        if start_next_msg or ready_to_edit or is_final_edit:
                            embed.description = response_contents[-1] if is_final_edit else (response_contents[-1] + STREAMING_INDICATOR)
                            embed.color = EMBED_COLOR_COMPLETE if msg_split_incoming or is_good_finish else EMBED_COLOR_INCOMPLETE

                            if start_next_msg:
                                await reply_helper(embed=embed, silent=True)
                            else:
                                await asyncio.sleep(EDIT_DELAY_SECONDS - time_delta)
                                await response_msgs[-1].edit(embed=embed)

                            last_task_time = datetime.now().timestamp()

            if use_plain_responses:
                for content in response_contents:
                    if HAS_LAYOUT_VIEW and HAS_TEXT_DISPLAY:
                        await reply_helper(view=LayoutView().add_item(TextDisplay(content=content)))
                    elif HAS_TEXT_DISPLAY:
                        view = View()
                        view.add_item(TextDisplay(content=content))
                        await reply_helper(view=view)
                    else:
                        await reply_helper(content=content)

    except Exception:
        logging.exception("Error while generating response")

    for response_msg in response_msgs:
        msg_nodes[response_msg.id].text = "".join(response_contents)
        msg_nodes[response_msg.id].lock.release()

    # Delete oldest MsgNodes (lowest message IDs) from the cache
    if (num_nodes := len(msg_nodes)) > MAX_MESSAGE_NODES:
        for msg_id in sorted(msg_nodes.keys())[: num_nodes - MAX_MESSAGE_NODES]:
            async with msg_nodes.setdefault(msg_id, MsgNode()).lock:
                msg_nodes.pop(msg_id, None)


async def main() -> None:
    await discord_bot.start(config["bot_token"])


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
