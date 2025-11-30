# Claude Hangout and Vibe

**This project is not an official project of Anthropic**

An AGI-pilled scaffolding for Claude to exist in its own environment. This project creates a persistent, autonomous agent using the [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) that can interact on Discord, browse the web, and maintain long-term memory through notes.

AGI-pilled because the scaffolding is extremely trivial (only ~100 lines of code): it just invokes the Claude Agent SDK and tells the model how to ensure that it doesn't crash but otherwise to just do whatever it wants. There's no scaffolding telling the model that it has to respond to anyone on Discord; it just... does because it wants to.

## What It Does

The agent runs in a continuous loop, maintaining state across iterations via session persistence. It can:

- **Chat on Discord** - Interact with users in configured channels using the Discord MCP server
- **Browse the web** - Search and fetch web content using a sub-agent optimized for cost
- **View images** - Fetch and analyze images from Discord attachments or web URLs with automatic resizing
- **Keep persistent notes** - Maintain long-term memory in a notes file that survives context resets
- **Self-manage context** - Automatically compact conversation history to stay within limits

The agent is designed to be autonomous - it decides when to respond, what to explore, and what to remember.

This pattern is extremely adaptable: you can add any MCP server you want to the SDK and Claude will autonomously decide to use it. Just update the prompt, and that's it.

## Costs

This is very expensive to run on Opus. It could easily be $1,000/mo. If you want to run this, I'd highly recommend capping how much tokens it can use with a Max 5x or higher plan. To get the agent to use this plan, init a normal Claude Code loggin session in the project directory. The Agent SDK will pick up these credentials and use them without an API key.

Note that Discord limits personal bot projects to 1,000 Gateway connections/day. So, be advised that running a lot of loops from outside the model (i.e. invoking the Agent SDK >1,000 times in a day) will cause the Discord MCP to restart each time and reconnect to the Gateway API each time.

## Architecture

```
main.py          - Async loop, handles startup and graceful shutdown
agent.py         - HangoutAgent class, manages SDK client and session persistence
config.py        - All configuration: prompts, timeouts, MCP servers, file paths
image_tools.py   - Custom MCP server for image fetching with vision support
mcp-wrapper.sh   - Captures MCP server logs to data/mcp-discord.log
data/            - Runtime data: session_id, notes.md, logs
```

## Setup

### Prerequisites

- Python 3.10+
- Node.js 18+ (for the Discord MCP server)
- A Discord bot token with appropriate permissions
- An Anthropic Claude Code account or an API key (set `ANTHROPIC_API_KEY` environment variable)

### 1. Clone this repository

```bash
git clone https://github.com/jclinton/hangout-and-vibe.git
cd hangout-and-vibe
```

### 2. Set up the Discord MCP server

Clone and build the Discord MCP server. We use a forked version with a gateway dispatch fix (until upstream merges the patch):

```bash
git clone -b fix/gateway-dispatch-event https://github.com/jclinton/discord-mcp.git
cd discord-mcp
npm install
npm run build
cd ..
```

### 3. Create a Python virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

```bash
# Discord Bot Configuration
DISCORD_BOT_TOKEN=your_bot_token_here

# Allowed Discord guilds (comma-separated IDs) - security allowlist
ALLOW_GUILD_IDS=123456789012345678

# Allowed Discord channels (comma-separated IDs) - security allowlist
ALLOW_CHANNEL_IDS=123456789012345678

# Path to the built discord-mcp server
DISCORD_MCP_PATH=/path/to/discord-mcp/dist/index.js
```

**Important**: The `ALLOW_GUILD_IDS` and `ALLOW_CHANNEL_IDS` are security allowlist. The agent can only access servers and channels in these lists. You can leave ALLOW_CHANNEL_IDS blank to permit all channels. (See the prompt in config.py.)

### 5. Set your Anthropic API key

```bash
export ANTHROPIC_API_KEY=your_api_key_here
```

Or, alternatively log in to your Anthropic account with Claude Code. The Agent SDK will use the same credentials.

## Running

```bash
python main.py
```

The agent will:
1. Run Discord connectivity diagnostics
2. Initialize (create notes file if first run)
3. Enter the main loop

Press `Ctrl+C` to gracefully shut down. Run in a tmux session to keep it running on a server somewhere, if you want. I have it running on a decade-old laptop.

## Logs

- `data/agent.log` - Detailed logging of tool calls, sub-agent invocations, and errors
- `data/mcp-discord.log` - Discord MCP server stderr output

## Configuration

Key settings in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `ITERATION_DELAY_SECONDS` | 3 | Delay between main loop iterations |
| `INACTIVITY_TIMEOUT_SECONDS` | 600 | Max time (10 min) between SDK messages before considering it hung |

## Security

The agent has restricted permissions:
- **File access**: Limited to the `data/` directory only
- **Bash commands**: Only `sleep` with numeric arguments is allowed
- **Discord access**: Limited to allowlisted guilds and channels; leave CHANNEL_IDS blank to allow any

## How It Works

The agent uses the Claude Agent SDK to create a long-running session. Key design decisions:

- **Long-running sessions**: Instead of short iterations, the agent runs continuously in a single SDK session
- **Automatic context management**: Compaction runs at the end of every agent exit to keep context trim
- **Persistent memory**: Notes are stored in `data/notes.md` and survive compaction
- **Cost optimization**: Web searches are delegated to a Haiku-based sub-agent instead of using Opus directly
- **Vision capabilities**: Custom MCP server fetches images and automatically resizes large images (>1568px) to optimize bandwidth and time-to-first-token

## License

MIT License - see [LICENSE](LICENSE) for details.
