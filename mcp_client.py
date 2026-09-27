"""Model Context Protocol (MCP) client manager supporting stdio and SSE transports."""
import asyncio
import concurrent.futures
import shlex
import threading
from typing import Any, Dict, List, Optional

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class McpClientManager:
    """Manages connection to an MCP server via stdio or SSE/HTTP and exposes synchronous tool calls."""

    def __init__(
        self,
        stdio_cmd: Optional[str] = None,
        server_url: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.stdio_cmd = stdio_cmd
        self.server_url = server_url
        self.timeout = timeout

        self.tools: List[Any] = []
        self.prompts: List[Any] = []
        self.resources: List[Any] = []

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._queue: Optional[asyncio.Queue] = None
        self._ready_event = threading.Event()
        self._error: Optional[Exception] = None

        if self.stdio_cmd or self.server_url:
            self._thread.start()
            if not self._ready_event.wait(timeout=15.0):
                if self._error:
                    raise self._error
                raise TimeoutError("MCP Client failed to connect within timeout.")

    def _run_event_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._worker())
        except Exception as e:
            self._error = e
            self._ready_event.set()

    async def _worker(self):
        self._queue = asyncio.Queue()

        if self.stdio_cmd:
            if isinstance(self.stdio_cmd, list):
                parts = self.stdio_cmd
            else:
                parts = shlex.split(self.stdio_cmd)
            cmd = parts[0]
            args = parts[1:] if len(parts) > 1 else []
            params = StdioServerParameters(command=cmd, args=args)
            cm = stdio_client(params)
        elif self.server_url:
            from mcp.client.sse import sse_client
            cm = sse_client(self.server_url)
        else:
            self._ready_event.set()
            return

        try:
            async with cm as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    # Discover tools
                    try:
                        tools_res = await session.list_tools()
                        self.tools = tools_res.tools
                    except Exception:
                        self.tools = []

                    # Discover prompts
                    try:
                        prompts_res = await session.list_prompts()
                        self.prompts = prompts_res.prompts
                    except Exception:
                        self.prompts = []

                    # Discover resources
                    try:
                        resources_res = await session.list_resources()
                        self.resources = resources_res.resources
                    except Exception:
                        self.resources = []

                    self._ready_event.set()

                    # Request dispatch loop
                    while True:
                        req = await self._queue.get()
                        if req is None:
                            break
                        action, payload, fut = req
                        try:
                            if action == "call_tool":
                                tool_name, arguments = payload
                                res = await session.call_tool(tool_name, arguments=arguments)
                                fut.set_result(res)
                            elif action == "get_prompt":
                                prompt_name, arguments = payload
                                res = await session.get_prompt(prompt_name, arguments=arguments)
                                fut.set_result(res)
                            elif action == "read_resource":
                                uri = payload
                                res = await session.read_resource(uri)
                                fut.set_result(res)
                            else:
                                fut.set_exception(ValueError(f"Unknown action: {action}"))
                        except Exception as exc:
                            fut.set_exception(exc)
                        finally:
                            self._queue.task_done()
        except Exception as e:
            self._error = e
            self._ready_event.set()

    def call_tool_sync(self, name: str, arguments: Dict[str, Any]) -> str:
        """Call an MCP tool synchronously and return string result."""
        if not self._queue:
            raise RuntimeError("MCP client is not connected.")

        fut: concurrent.futures.Future = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(
            self._queue.put(("call_tool", (name, arguments), fut)), self._loop
        )
        result = fut.result(timeout=self.timeout)

        # Extract textual representation from result content
        if hasattr(result, "content") and result.content:
            parts = []
            for item in result.content:
                if hasattr(item, "text"):
                    parts.append(item.text)
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(result)

    def generate_manual(self) -> str:
        """Generate formatted documentation of all available tools for LLM prompts."""
        lines = [
            "### Available Tools (MCP Server)",
            "The following Python functions are directly callable in your code blocks:",
            "",
        ]

        if not self.tools:
            lines.append("No MCP tools currently connected.")
            return "\n".join(lines)

        for tool in self.tools:
            name = tool.name
            desc = tool.description or "No description provided."
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            required = set(schema.get("required", []))

            # Build signature
            param_strs = []
            for param_name, param_info in props.items():
                ptype = param_info.get("type", "Any")
                if ptype == "string":
                    ptype = "str"
                elif ptype == "integer":
                    ptype = "int"
                elif ptype == "boolean":
                    ptype = "bool"

                if param_name in required:
                    param_strs.append(f"{param_name}: {ptype}")
                else:
                    default_val = param_info.get("default", "None")
                    param_strs.append(f"{param_name}: {ptype} = {default_val}")

            sig = f"def {name}({', '.join(param_strs)}) -> str:"
            lines.append(f"```python\n{sig}\n    \"\"\"{desc}\"\"\"\n```")
            lines.append("")

        lines.extend([
            "### Built-in Sandbox Construct",
            "```python",
            "def final_answer(solution: str) -> None:",
            "    \"\"\"Call this function when the task is solved. Pass the final solution code (for MBPP) or git patch (for SWE-bench).\"\"\"",
            "```",
        ])

        return "\n".join(lines)

    def close(self):
        """Shutdown client connection."""
        if self._queue and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)
