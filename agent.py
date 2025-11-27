from pathlib import Path
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ResultMessage,
)
from config import (
    DATA_DIR,
    SESSION_FILE,
    MCP_SERVERS,
    SYSTEM_PROMPT,
    INIT_PROMPT,
    IDLE_PROMPT,
)


class HangoutAgent:
    """An agent that hangs out, chats on Discord, browses the web, and keeps notes."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.session_id = self._load_session_id()

    def _load_session_id(self) -> str | None:
        """Load persisted session ID if it exists."""
        if SESSION_FILE.exists():
            sid = SESSION_FILE.read_text().strip()
            return sid if sid else None
        return None

    def _save_session_id(self, session_id: str):
        """Persist session ID for future runs."""
        SESSION_FILE.write_text(session_id)
        self.session_id = session_id

    def _get_options(self) -> ClaudeAgentOptions:
        """Build options for the agent, including session resume if available."""
        return ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers=MCP_SERVERS,
            max_turns=None,  # Let Claude decide when it's done with this iteration
            resume=self.session_id,
            allowed_tools=[
                "Read",
                "Write",
                "Glob",
                "WebFetch",
                "WebSearch",
                "mcp__discord__*",
            ],
            cwd=str(DATA_DIR),
        )

    @property
    def is_initialized(self) -> bool:
        """Check if the agent has been initialized (has a session)."""
        return self.session_id is not None

    async def initialize(self):
        """One-time setup - creates session and initial notes."""
        print("=== Initializing agent ===")
        await self._run_query(INIT_PROMPT)

    async def run_iteration(self):
        """Single iteration of the main loop."""
        print("\n=== Running iteration ===")
        await self._run_query(IDLE_PROMPT)

    async def _run_query(self, prompt: str):
        """Execute a query, capturing session ID from result."""
        async with ClaudeSDKClient(options=self._get_options()) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    self._save_session_id(msg.session_id)
                    print(f"\n[Session: {msg.session_id[:12]}... | Turns: {msg.num_turns}]")
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(block.text)
