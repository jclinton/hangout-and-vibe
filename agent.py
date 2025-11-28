import asyncio
import logging
from pathlib import Path
from claude_agent_sdk import (
    AgentDefinition,
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ResultMessage,
    SystemMessage,
    UserMessage,
    ToolUseBlock,
    ToolResultBlock,
)
from config import (
    DATA_DIR,
    SESSION_FILE,
    MCP_SERVERS,
    SYSTEM_PROMPT,
    DIAGNOSTIC_PROMPT,
    INIT_PROMPT,
    IDLE_PROMPT,
    QUERY_TIMEOUT_SECONDS,
    MAX_RETRIES,
)

# Set up logging
logger = logging.getLogger("hangout")


class HangoutAgent:
    """An agent that hangs out, chats on Discord, browses the web, and keeps notes."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.session_id = self._load_session_id()
        logger.info(f"Agent initialized. Session ID: {self.session_id or 'None (new session)'}")
        logger.debug(f"MCP Servers config: {MCP_SERVERS}")

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

    def _handle_stderr(self, message: str):
        """Log stderr output from the SDK/MCP servers."""
        # Log at debug level to avoid cluttering console, but capture in file
        logger.debug(f"SDK STDERR: {message.rstrip()}")

    def _can_use_tool(self, tool_name: str, tool_input: dict) -> str:
        """Restrict file operations to the data directory only."""
        # Resolve the allowed directory to an absolute path
        allowed_dir = DATA_DIR.resolve()

        # Tools that access file paths
        if tool_name in ["Read", "Write", "Glob"]:
            # Get the path from the tool input
            path_str = tool_input.get("file_path") or tool_input.get("path", "")
            if not path_str:
                # Glob without path uses cwd, which is DATA_DIR - allow it
                if tool_name == "Glob":
                    return "allow"
                return "deny"

            # Resolve to absolute path
            try:
                requested_path = Path(path_str).resolve()
            except Exception:
                logger.warning(f"Invalid path in {tool_name}: {path_str}")
                return "deny"

            # Check if path is within allowed directory
            try:
                requested_path.relative_to(allowed_dir)
                return "allow"
            except ValueError:
                logger.warning(f"Blocked {tool_name} outside data dir: {path_str}")
                return "deny"

        # Bash - only allow sleep command with numeric argument
        if tool_name == "Bash":
            import re
            command = tool_input.get("command", "")
            # Only allow "sleep" followed by a number (integer or decimal)
            if re.fullmatch(r"sleep\s+\d+(\.\d+)?", command.strip()):
                return "allow"
            logger.warning(f"Blocked Bash command: {command}")
            return "deny"

        # Allow all other tools (Discord MCP, WebFetch, WebSearch, etc.)
        return "allow"

    def _get_options(self) -> ClaudeAgentOptions:
        """Build options for the agent, including session resume if available."""
        return ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers=MCP_SERVERS,
            model="claude-opus-4-5-20251101",
            max_turns=None,  # Let Claude decide when it's done with this iteration
            resume=self.session_id,
            fork_session=False,  # Direct resume - let compaction manage context size
            permission_mode="bypassPermissions",  # Auto-approve tool usage for autonomous operation
            stderr=self._handle_stderr,  # Capture SDK/MCP stderr output
            can_use_tool=self._can_use_tool,  # Restrict file access to data/ directory
            allowed_tools=[
                "Read",
                "Write",
                "Glob",
                "Bash",  # For sleep command only (restricted in _can_use_tool)
                "WebFetch",
                "WebSearch",
                "mcp__discord__*",
            ],
            agents={
                "web_researcher": AgentDefinition(
                    description="Use this agent for web searches and fetching web content from URLs",
                    prompt="You are a web research assistant. Search the web and fetch content as requested. Return the relevant information concisely.",
                    tools=["WebSearch", "WebFetch"],
                    model="haiku",
                ),
            },
            cwd=str(DATA_DIR),
        )

    @property
    def is_initialized(self) -> bool:
        """Check if the agent has been initialized (has a session)."""
        return self.session_id is not None

    async def run_diagnostics(self):
        """Run Discord connectivity diagnostics."""
        logger.info("=== Running Discord diagnostics ===")
        await self._run_query(DIAGNOSTIC_PROMPT)

    async def initialize(self):
        """One-time setup - creates session and initial notes."""
        logger.info("=== Initializing agent ===")
        await self._run_query(INIT_PROMPT)

    async def run_iteration(self):
        """Single iteration of the main loop."""
        logger.info("=== Running iteration ===")
        await self._run_query(IDLE_PROMPT)

    async def compact(self):
        """Trigger context compaction to reduce token usage."""
        logger.info("=== Triggering compaction ===")
        async with ClaudeSDKClient(options=self._get_options()) as client:
            await client.query("/compact")
            async for msg in client.receive_response():
                self._log_message(msg)
                if isinstance(msg, ResultMessage):
                    self._save_session_id(msg.session_id)
                    logger.info(f"Compaction complete. New session: {msg.session_id[:12]}...")

    async def _run_query(self, prompt: str, _compaction_attempted: bool = False):
        """Execute a query with timeout and retry logic."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = await asyncio.wait_for(
                    self._execute_query(prompt),
                    timeout=QUERY_TIMEOUT_SECONDS
                )
                # Check if we hit a "prompt too long" error
                if result.get("prompt_too_long") and not _compaction_attempted:
                    logger.warning("Prompt too long - attempting compaction...")
                    await self.compact()
                    logger.info("Retrying original query after compaction...")
                    return await self._run_query(prompt, _compaction_attempted=True)
                return  # Success, exit retry loop
            except asyncio.TimeoutError:
                logger.warning(f"Query timed out after {QUERY_TIMEOUT_SECONDS}s (attempt {attempt}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES:
                    logger.info("Retrying...")
                    await asyncio.sleep(2)  # Brief pause before retry
                else:
                    logger.error(f"Query failed after {MAX_RETRIES} attempts")
                    raise
            except Exception as e:
                logger.error(f"Query error (attempt {attempt}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES:
                    logger.info("Retrying...")
                    await asyncio.sleep(2)
                else:
                    raise

    async def _execute_query(self, prompt: str) -> dict:
        """Execute a single query attempt. Returns dict with status info."""
        logger.debug(f"Query prompt: {prompt[:100]}...")
        result = {"prompt_too_long": False}

        async with ClaudeSDKClient(options=self._get_options()) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                self._log_message(msg)

                if isinstance(msg, ResultMessage):
                    self._save_session_id(msg.session_id)
                    logger.info(f"Session: {msg.session_id[:12]}... | Turns: {msg.num_turns} | Cost: ${msg.total_cost_usd:.4f}")
                    print(f"\n[Session: {msg.session_id[:12]}... | Turns: {msg.num_turns}]")
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(block.text)
                            # Detect "prompt too long" error
                            if "prompt is too long" in block.text.lower():
                                result["prompt_too_long"] = True
                                logger.error("Detected 'prompt is too long' error")

        return result

    def _log_message(self, msg):
        """Log detailed information about each message from the SDK."""
        if isinstance(msg, SystemMessage):
            logger.debug(f"SystemMessage: subtype={msg.subtype}")
            if msg.subtype == "init" and hasattr(msg, "data"):
                tools = msg.data.get("tools", [])
                mcp_tools = [t for t in tools if t.startswith("mcp__")]
                logger.info(f"Available MCP tools: {len(mcp_tools)} tools")
                logger.debug(f"MCP tools list: {mcp_tools}")

        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    # Check if this is a sub-agent invocation
                    if block.name == "Task" and isinstance(block.input, dict):
                        subagent = block.input.get("subagent_type", "unknown")
                        desc = block.input.get("description", "")
                        logger.info(f"SUBAGENT CALL: {subagent} - {desc}")
                    else:
                        logger.info(f"TOOL CALL: {block.name}")
                    logger.debug(f"  Input: {block.input}")
                elif isinstance(block, TextBlock):
                    # Log first 200 chars of assistant text
                    preview = block.text[:200] + "..." if len(block.text) > 200 else block.text
                    logger.debug(f"Assistant text: {preview}")

        elif isinstance(msg, UserMessage):
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    # This is where tool results come back - critical for debugging!
                    content = block.content
                    is_error = getattr(block, "is_error", False)

                    if is_error:
                        logger.error(f"TOOL ERROR: {content}")
                    else:
                        # Log tool result, truncating if very long
                        if isinstance(content, str):
                            preview = content[:500] + "..." if len(content) > 500 else content
                        elif isinstance(content, list):
                            # MCP tools often return list of content blocks
                            preview = str(content)[:500] + "..." if len(str(content)) > 500 else str(content)
                        else:
                            preview = str(content)[:500]
                        logger.info(f"TOOL RESULT: {preview}")

        elif isinstance(msg, ResultMessage):
            logger.info(f"Query complete: turns={msg.num_turns}, cost=${msg.total_cost_usd:.4f}, error={msg.is_error}")
            if hasattr(msg, "usage"):
                logger.debug(f"Token usage: {msg.usage}")
