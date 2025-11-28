# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Hangout and Vibe is a Python application that creates a persistent environment for a Claude agent using the Claude Agent SDK. The agent runs in a continuous loop, maintaining state across iterations via session persistence and a notes file for long-term memory.

The goal is to give Claude an autonomous operating environment where it can choose to interact on Discord, to surf the web, to clean its notes, or to self-reflect.

## Running the Agent

```bash
# Install dependencies
pip install -r requirements.txt

# Run the agent
python main.py
```

The agent requires environment variables (via `.env` file):
- `DISCORD_BOT_TOKEN` - Discord bot token
- `DISCORD_MCP_PATH` - Path to the Discord MCP server dist/index.js
- `ALLOW_GUILD_IDS` - Comma-separated Discord guild IDs (security whitelist)
- `ALLOW_CHANNEL_IDS` - Comma-separated Discord channel IDs (security whitelist)
- `API_TIMEOUT_MS` - API timeout in milliseconds (optional, default 90000)

## Architecture

**Main loop** (`main.py`): Async loop that calls `agent.run_iteration()` every 3 seconds. Handles graceful shutdown on SIGINT/SIGTERM. On startup, runs compaction and diagnostics before entering the main loop.

**Agent** (`agent.py`): `HangoutAgent` class manages:
- Session persistence (saves/loads session ID from `data/session_id`)
- Claude SDK client configuration with MCP servers and allowed tools
- Query timeout and retry logic (configurable in `config.py`)
- Automatic compaction when "prompt too long" errors occur
- Sub-agent delegation (web_researcher uses Haiku for cost optimization)

**Configuration** (`config.py`): All constants including:
- File paths (`DATA_DIR`, `SESSION_FILE`, `NOTES_FILE`)
- Timing settings (`ITERATION_DELAY_SECONDS`, `QUERY_TIMEOUT_SECONDS`, `MAX_RETRIES`)
- MCP server config (Discord integration)
- Prompts (`SYSTEM_PROMPT`, `INIT_PROMPT`, `IDLE_PROMPT`, `DIAGNOSTIC_PROMPT`)

**Logs** (`data/agent.log`): Detailed logging of all tool calls, sub-agent invocations, and errors.

## Key Design Patterns

- **Session forking**: Uses `fork_session=True` to create new session IDs each iteration while preserving context history
- **Persistent memory**: Uses `notes.md` in the data directory as long-term memory that survives context resets
- **MCP integration**: Discord access via MCP server, configured in `MCP_SERVERS` dict. Note: `ALLOW_GUILD_IDS` and `ALLOW_CHANNEL_IDS` are security whitelists, not targets
- **Allowed tools whitelist**: Limited to Read, Write, Glob, WebFetch, WebSearch, and Discord MCP tools
- **Cost optimization**: Web searches delegated to `web_researcher` sub-agent running on Haiku instead of Opus
- **Error recovery**: Automatic retry on timeout (3 attempts), automatic compaction on "prompt too long" errors

## Important SDK Notes

- **permission_mode**: Must be set to `"bypassPermissions"` for autonomous operation, otherwise MCP tools require interactive approval
- **fork_session**: Recommended for long-running agents to avoid context overflow on session resume
- **Compaction**: Can be triggered manually with `/compact` command; runs automatically on startup
- **Sub-agents**: Defined via `agents` parameter in `ClaudeAgentOptions`, use short model names (`"haiku"`, `"opus"`, `"sonnet"`)

## Debugging

Check `data/agent.log` for:
- `TOOL CALL: <name>` - Direct tool invocations
- `SUBAGENT CALL: <type> - <description>` - Sub-agent delegations
- `TOOL ERROR: <message>` - Tool failures
- `Query timed out` - Timeout issues
- `prompt is too long` - Context overflow (triggers auto-compaction)
