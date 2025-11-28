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
- `ALLOW_GUILD_IDS` - Comma-separated Discord guild IDs
- `ALLOW_CHANNEL_IDS` - Comma-separated Discord channel IDs

## Architecture

**Main loop** (`main.py`): Async loop that calls `agent.run_iteration()` every 3 seconds. Handles graceful shutdown on SIGINT/SIGTERM.

**Agent** (`agent.py`): `HangoutAgent` class manages:
- Session persistence (saves/loads session ID from `data/session_id`)
- Claude SDK client configuration with MCP servers and allowed tools
- Two modes: `initialize()` (first run) and `run_iteration()` (ongoing loop)

**Configuration** (`config.py`): All constants including:
- File paths (`DATA_DIR`, `SESSION_FILE`, `NOTES_FILE`)
- MCP server config (Discord integration)
- Prompts (`SYSTEM_PROMPT`, `INIT_PROMPT`, `IDLE_PROMPT`)

## Key Design Patterns

- **Session resumption**: The agent persists its session ID to maintain conversation context across restarts
- **Persistent memory**: Uses `notes.md` in the data directory as long-term memory that survives context resets
- **MCP integration**: Discord access via MCP server, configured in `MCP_SERVERS` dict
- **Allowed tools whitelist**: Limited to Read, Write, Glob, WebFetch, WebSearch, and Discord MCP tools
