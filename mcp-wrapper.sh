#!/bin/bash
# Wrapper script to capture MCP server stderr to a log file
# while still passing it through for the SDK

# Use set -a to auto-export all variables when sourcing .env
set -a
source "$(dirname "$0")/.env"
set +a

LOGFILE="${MCP_LOG_FILE:-data/mcp-discord.log}"
exec node "$@" 2> >(tee -a "$LOGFILE" >&2)
