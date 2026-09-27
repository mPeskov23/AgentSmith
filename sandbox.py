"""Execution Sandbox providing security enforcement, MCP tool wrappers, and interactive REPL."""
import argparse
import ast
import io
import json
import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional

from models import SandboxConfig
from mcp_client import McpClientManager
from sandbox_security import (
    FinalAnswerSignal,
    SandboxSecurityContext,
    SandboxSecurityViolation,
    SandboxTimeoutError,
)

MAX_OUTPUT_SIZE_BYTES = 50 * 1024  # 50 KB output truncation limit


class ExecutionSandbox:
    """Secure Python execution environment with injected MCP tools and final_answer."""

    def __init__(
        self,
        config: Optional[SandboxConfig] = None,
        mcp_client: Optional[McpClientManager] = None,
    ):
        self.config = config or SandboxConfig()
        self.mcp_client = mcp_client
        self.final_answer: Optional[str] = None
        self.has_finished: bool = False

        # Persistent execution namespace
        self.namespace: Dict[str, Any] = {}
        self._init_namespace()

    def _init_namespace(self):
        """Initialize the sandbox namespace with builtins, final_answer, and MCP tool wrappers."""
        # Builtin final_answer construct (NOT an MCP tool)
        def _final_answer_callable(answer: Any) -> None:
            self.final_answer = str(answer)
            self.has_finished = True
            raise FinalAnswerSignal(str(answer))

        self.namespace["final_answer"] = _final_answer_callable

        # Bind MCP tools dynamically if connected
        if self.mcp_client and self.mcp_client.tools:
            for tool in self.mcp_client.tools:
                tool_name = tool.name
                schema = tool.inputSchema or {}
                properties = list(schema.get("properties", {}).keys())

                def _create_wrapper(t_name: str, prop_names: List[str]):
                    def _tool_wrapper(*args, **kwargs):
                        # Convert positional args to named arguments
                        merged_kwargs = dict(kwargs)
                        for idx, val in enumerate(args):
                            if idx < len(prop_names):
                                merged_kwargs[prop_names[idx]] = val

                        # MCP actions execute outside the sandbox security domain
                        sec_ctx = getattr(self, "active_security_context", None)
                        if sec_ctx:
                            with sec_ctx.pause():
                                res = self.mcp_client.call_tool_sync(t_name, merged_kwargs)
                        else:
                            res = self.mcp_client.call_tool_sync(t_name, merged_kwargs)
                        return res
                    _tool_wrapper.__name__ = t_name
                    _tool_wrapper.__doc__ = tool.description or ""
                    return _tool_wrapper

                self.namespace[tool_name] = _create_wrapper(tool_name, properties)

    def get_manual(self) -> str:
        """Dynamically generate the sandbox manual for the LLM prompt."""
        if self.mcp_client:
            return self.mcp_client.generate_manual()
        return (
            "### Available Tools\n"
            "No external MCP server is currently connected.\n\n"
            "### Built-in Sandbox Construct\n"
            "```python\ndef final_answer(solution: str) -> None:\n    \"\"\"Call this function when the task is solved.\"\"\"\n```"
        )

    def execute(self, code: str) -> str:
        """Execute Python code within sandbox security boundaries.

        Returns string observation (stdout, return value, or explicit error feedback).
        """
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        # Syntax check before execution for clear feedback
        try:
            parsed_ast = ast.parse(code)
        except SyntaxError as e:
            return f"SyntaxError in code block: {e.msg} at line {e.lineno}"

        has_output = False
        result_str = ""

        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                sec_ctx = SandboxSecurityContext(
                    authorized_imports=self.config.authorized_imports,
                    allowed_directories=self.config.allowed_directories,
                    max_execution_time_seconds=self.config.max_execution_time_seconds,
                    max_memory_mb=self.config.max_memory_mb,
                )
                self.active_security_context = sec_ctx
                with sec_ctx:
                    # If code is a single expression, evaluate and print it
                    if len(parsed_ast.body) == 1 and isinstance(parsed_ast.body[0], ast.Expr):
                        expr_ast = ast.Expression(parsed_ast.body[0].value)
                        compiled = compile(expr_ast, filename="<sandbox>", mode="eval")
                        val = eval(compiled, self.namespace)
                        if val is not None:
                            print(repr(val))
                    else:
                        compiled = compile(parsed_ast, filename="<sandbox>", mode="exec")
                        exec(compiled, self.namespace)

        except (KeyboardInterrupt, SystemExit):
            # Propagate exceptions that control program flow
            raise
        except FinalAnswerSignal as fa:
            return f"Final answer submitted: {fa.answer}"
        except SandboxTimeoutError as te:
            partial_out = stdout_buf.getvalue() + stderr_buf.getvalue()
            return f"Execution hit the timeout and output is partial: {te}\n{partial_out}"
        except (SandboxSecurityViolation, ImportError) as sec_err:
            return f"Sandbox Security Error: {sec_err}"
        except MemoryError:
            return "MemoryError: Execution exceeded the memory limit (max_memory_mb)."
        except Exception as e:
            tb = traceback.format_exc()
            return f"Runtime Error: {type(e).__name__}: {e}\n{tb}"

        # Collect stdout and stderr
        out_content = stdout_buf.getvalue()
        err_content = stderr_buf.getvalue()
        combined = out_content
        if err_content:
            combined = f"{combined}\nSTDERR:\n{err_content}" if combined else f"STDERR:\n{err_content}"

        clean_combined = combined.strip()
        if not clean_combined:
            clean_combined = "(Code executed successfully with no output)"

        # Enforce size limits
        if len(clean_combined.encode("utf-8")) > MAX_OUTPUT_SIZE_BYTES:
            truncated = clean_combined[:MAX_OUTPUT_SIZE_BYTES]
            clean_combined = f"Tool output was truncated due to size limits:\n{truncated}\n... (truncated)"

        return clean_combined

    def close(self):
        """Release MCP client resources."""
        if self.mcp_client:
            self.mcp_client.close()


def run_repl(sandbox: ExecutionSandbox):
    """Run interactive REPL-style command-line mode."""
    print("Agent Smith Interactive Sandbox REPL")
    print(f"Allowed directories: {sandbox.config.allowed_directories}")
    print("Type 'exit' or Ctrl+D to exit.")
    print("-" * 50)

    while True:
        try:
            line = input(">>> ")
            if line.strip() in ("exit", "exit()", "quit", "quit()"):
                print("Exiting sandbox.")
                break
            if not line.strip():
                continue

            output = sandbox.execute(line)
            print(output)
            if sandbox.has_finished:
                print(f"Task completed! Final answer: {sandbox.final_answer}")
                break
        except (KeyboardInterrupt, EOFError):
            print("\nExiting sandbox.")
            break


def cli_main():
    """CLI entry point for uv run sandbox."""
    parser = argparse.ArgumentParser(description="Agent Smith Sandbox CLI")
    parser.add_argument("config_file", nargs="?", default=None, help="Path to sandbox_template.json")
    parser.add_argument("--mcp-stdio", dest="mcp_stdio", default=None, help="Command to run MCP server via stdio")
    parser.add_argument("--mcp-server", dest="mcp_server", default=None, help="URL of remote MCP server")

    args = parser.parse_args()

    # Load configuration
    config = SandboxConfig()
    if args.config_file and os.path.isfile(args.config_file):
        with open(args.config_file, "r", encoding="utf-8") as f:
            cfg_dict = json.load(f)
            config = SandboxConfig.model_validate(cfg_dict)

    # Initialize MCP client if requested
    mcp_client = None
    if args.mcp_stdio or args.mcp_server:
        mcp_client = McpClientManager(
            stdio_cmd=args.mcp_stdio,
            server_url=args.mcp_server,
        )

    sandbox = ExecutionSandbox(config=config, mcp_client=mcp_client)
    try:
        run_repl(sandbox)
    finally:
        sandbox.close()


if __name__ == "__main__":
    cli_main()
