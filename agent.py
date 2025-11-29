import asyncio
import logging
import re
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
from claude_agent_sdk.types import (
    HookMatcher,
    PreToolUseHookInput,
    HookContext,
    SyncHookJSONOutput,
)
from config import (
    DATA_DIR,
    SESSION_FILE,
    MCP_SERVERS,
    SYSTEM_PROMPT,
    DIAGNOSTIC_PROMPT,
    INIT_PROMPT,
    IDLE_PROMPT,
    INACTIVITY_TIMEOUT_SECONDS,
)
from image_tools import image_mcp_server

# Set up logging
logger = logging.getLogger("hangout")


async def pre_tool_use_hook(
    input_data: PreToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> SyncHookJSONOutput:
    """
    PreToolUse hook that enforces permission rules.

    This hook runs BEFORE bypassPermissions mode is checked, so it can
    effectively control tool access even in autonomous operation.

    Returns a decision of 'allow' or 'deny' for each tool use.
    """
    tool_name = input_data["tool_name"]
    tool_input = input_data["tool_input"]
    allowed_dir = DATA_DIR.resolve()

    # File access tools - restrict to data directory only
    if tool_name in ["Read", "Write", "Glob"]:
        path_str = tool_input.get("file_path") or tool_input.get("path", "")
        if not path_str:
            # Glob without path uses cwd, which is DATA_DIR - allow it
            if tool_name == "Glob":
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                    }
                }
            logger.warning(f"Blocked {tool_name}: no path provided")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{tool_name} requires a file path",
                }
            }

        # Resolve to absolute path
        try:
            requested_path = Path(path_str).resolve()
        except Exception:
            logger.warning(f"Invalid path in {tool_name}: {path_str}")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Invalid path: {path_str}",
                }
            }

        # Check if path is within allowed directory
        try:
            requested_path.relative_to(allowed_dir)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }
        except ValueError:
            logger.warning(f"Blocked {tool_name} outside data dir: {path_str}")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Access denied: path must be within {allowed_dir}",
                }
            }

    # Bash - only allow sleep command with numeric argument
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        # Only allow "sleep" followed by a number (integer or decimal)
        if re.fullmatch(r"sleep\s+\d+(\.\d+)?", command.strip()):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }
        logger.warning(f"Blocked Bash command: {command}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Only 'sleep <number>' commands are allowed",
            }
        }

    # Allow all other tools (Discord MCP, WebFetch, WebSearch, image_tools MCP, etc.)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


class HangoutAgent:
    """An agent that hangs out, chats on Discord, browses the web, and keeps notes."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.session_id = self._load_session_id()
        self._client: ClaudeSDKClient | None = None  # Long-lived client instance
        self._query_in_progress = False  # Track if a query is running
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

    def _get_options(self) -> ClaudeAgentOptions:
        """Build options for the agent, including session resume if available."""
        # Combine external MCP servers with in-process SDK servers
        all_mcp_servers = {
            **MCP_SERVERS,
            "image_tools": image_mcp_server,
        }
        return ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers=all_mcp_servers,
            model="claude-opus-4-5-20251101",
            max_turns=None,  # Let Claude decide when it's done with this iteration
            resume=self.session_id,
            fork_session=False,  # Direct resume - let compaction manage context size
            permission_mode="default",  # Use default mode - hooks handle auto-approval
            stderr=self._handle_stderr,  # Capture SDK/MCP stderr output
            max_buffer_size=10 * 1024 * 1024,  # 10MB buffer for large images
            # PreToolUse hook enforces permission rules (runs before permission mode check)
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        matcher=None,  # Match all tools
                        hooks=[pre_tool_use_hook],
                    )
                ]
            },
            allowed_tools=[
                "Read",
                "Write",
                "Glob",
                "Bash",  # For sleep command only (restricted in hook)
                "WebFetch",
                "WebSearch",
                "mcp__discord__*",
                "mcp__image_tools__*",  # For fetching images from URLs
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

    async def start(self):
        """Start the agent by initializing the long-lived client."""
        if self._client is not None:
            logger.warning("Agent already started")
            return
        logger.info("Starting agent client...")
        self._client = ClaudeSDKClient(options=self._get_options())
        await self._client.__aenter__()
        logger.info("Agent client started")

    async def stop(self):
        """Stop the agent and clean up the client."""
        if self._client is None:
            return
        logger.info("Stopping agent client...")
        try:
            if self._query_in_progress:
                await self.interrupt()
            await self._client.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"Error during client cleanup: {e}")
        finally:
            self._client = None
            logger.info("Agent client stopped")

    async def interrupt(self):
        """Interrupt any active query. Call this on shutdown signals."""
        if self._client and self._query_in_progress:
            logger.info("Interrupting active query...")
            try:
                await self._client.interrupt()
                logger.info("Interrupt sent successfully")
            except Exception as e:
                logger.warning(f"Error sending interrupt: {e}")

    async def run_diagnostics(self):
        """Run Discord connectivity diagnostics."""
        logger.info("=== Running Discord diagnostics ===")
        await self._run_query(DIAGNOSTIC_PROMPT)

    async def initialize(self):
        """One-time setup - creates session and initial notes."""
        logger.info("=== Initializing agent ===")
        await self._run_query(INIT_PROMPT)

    async def _restart_client(self):
        """Restart the client to get a fresh session when compaction fails.

        This recreates the ClaudeSDKClient which will also restart MCP servers.
        Only used as a last resort when context is full and compaction doesn't help.
        """
        logger.warning("Restarting client for fresh session (MCP servers will restart)")
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
        self.session_id = None

        # Stop the old client
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error stopping old client: {e}")
            self._client = None

        # Create fresh client - _get_options() will have resume=None now
        logger.info("Creating fresh client instance...")
        self._client = ClaudeSDKClient(options=self._get_options())
        await self._client.__aenter__()
        logger.info("Fresh client ready")

    async def compact(self) -> bool:
        """Trigger context compaction to reduce token usage.

        Returns True if compaction succeeded, False if it failed.
        """
        logger.info("=== Triggering compaction ===")
        if self._client is None:
            logger.error("Cannot compact: client not started")
            return False
        try:
            await self._client.query("/compact")
            async for msg in self._client.receive_response():
                self._log_message(msg)
                if isinstance(msg, ResultMessage):
                    if msg.is_error:
                        logger.error(f"Compaction returned error: {msg}")
                        return False
                    self._save_session_id(msg.session_id)
                    logger.info(f"Compaction complete. New session: {msg.session_id[:12]}...")
                    return True
        except Exception as e:
            logger.error(f"Compaction failed with exception: {e}")
            return False
        return False  # No ResultMessage received

    async def _run_query(self, prompt: str, _retried: bool = False):
        """Execute a query, handling prompt-too-long errors with compaction or restart."""
        result = await self._execute_query(prompt)
        # Check if we hit a "prompt too long" error
        if result.get("prompt_too_long"):
            if _retried:
                # Already retried once - give up to avoid infinite loop
                logger.error("Prompt still too long after restart - giving up")
                return
            logger.warning("Prompt too long - attempting compaction...")
            await self.check_context_size()
            compact_succeeded = await self.compact()
            if compact_succeeded:
                logger.info("Retrying original query after compaction...")
                return await self._run_query(prompt, _retried=True)
            else:
                # Compaction failed - restart client as last resort
                logger.warning("Compaction failed - restarting client")
                await self._restart_client()
                return await self._run_query(prompt, _retried=True)

    async def check_context_size(self):
        """Query the SDK for current context size using /context command."""
        logger.info("=== Checking context size ===")
        if self._client is None:
            logger.error("Cannot check context: client not started")
            return
        await self._client.query("/context")
        async for msg in self._client.receive_response():
            # Log ALL message types to see what /context returns
            logger.info(f"Context response: type={type(msg).__name__}, msg={msg}")
            self._log_message(msg)

    async def _execute_query(self, prompt: str) -> dict:
        """Execute a single query attempt with inactivity monitoring.

        Uses per-message timeout instead of global timeout. If no messages
        arrive for INACTIVITY_TIMEOUT_SECONDS, raises asyncio.TimeoutError.
        """
        logger.info(f"Query prompt ({len(prompt)} chars): {prompt[:100]}...")
        result = {"prompt_too_long": False}

        if self._client is None:
            raise RuntimeError("Cannot execute query: client not started")

        self._query_in_progress = True
        try:
            await self._client.query(prompt)
            response_iter = self._client.receive_response()

            while True:
                try:
                    # Wait for next message with inactivity timeout
                    msg = await asyncio.wait_for(
                        response_iter.__anext__(),
                        timeout=INACTIVITY_TIMEOUT_SECONDS
                    )
                    self._log_message(msg)

                    if isinstance(msg, ResultMessage):
                        self._save_session_id(msg.session_id)
                        logger.info(f"Session: {msg.session_id[:12]}... | Turns: {msg.num_turns} | Cost: ${msg.total_cost_usd:.4f}")
                        print(f"\n[Session: {msg.session_id[:12]}... | Turns: {msg.num_turns}]")
                        break
                    elif isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                print(block.text)
                                # Detect "prompt too long" error
                                if "prompt is too long" in block.text.lower():
                                    result["prompt_too_long"] = True
                                    logger.error("Detected 'prompt is too long' error")
                except StopAsyncIteration:
                    break
                # asyncio.TimeoutError propagates up to caller
        finally:
            self._query_in_progress = False

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

    async def run_iteration(self):
        """Run a single iteration of the main loop."""
        logger.info("=== Running iteration ===")
        await self._run_query(IDLE_PROMPT)
