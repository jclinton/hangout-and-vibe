#!/bin/bash
# Wrapper script to capture MCP server stderr to a log file
# while still passing it through for the SDK

LOGFILE="${MCP_LOG_FILE:-data/mcp-discord.log}"
exec node "$@" 2> >(tee -a "$LOGFILE" >&2)
