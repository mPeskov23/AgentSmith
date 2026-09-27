"""MBPP MCP Server exposing test execution, task inspection, resources, and prompts."""
import json
import os
import sys
import traceback
from typing import Optional

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("mbpp-tools")

# Current task context loaded from environment or task file
CURRENT_TASK_FILE = os.environ.get("MBPP_TASK_FILE", "")
_candidate_code = ""


def _load_current_task() -> dict:
    if CURRENT_TASK_FILE and os.path.isfile(CURRENT_TASK_FILE):
        try:
            with open(CURRENT_TASK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


@mcp.tool()
def run_tests() -> str:
    """Run public test assertions against the current candidate code."""
    global _candidate_code
    if not _candidate_code:
        return "Error: No candidate code set. Call test_code(code) or set_candidate_code(code) first."

    return test_code(_candidate_code)


@mcp.tool()
def set_candidate_code(code: str) -> str:
    """Set the candidate solution code to be tested by run_tests()."""
    global _candidate_code
    _candidate_code = code
    return f"Candidate code updated ({len(code)} chars)."


@mcp.tool()
def test_code(code: str) -> str:
    """Execute Python code against the public test suite and return results."""
    global _candidate_code
    _candidate_code = code
    task = _load_current_task()

    test_imports = task.get("test_imports", [])
    test_list = task.get("test_list", [])

    # If no task file loaded, try executing the code itself
    full_code = code + "\n"
    if test_imports:
        full_code += "\n".join(test_imports) + "\n"
    if test_list:
        full_code += "\n".join(test_list) + "\n"

    # Execute in a safe isolated execution dictionary
    exec_scope = {}
    try:
        exec(full_code, exec_scope)
        return "All tests passed!"
    except AssertionError as e:
        tb = traceback.format_exc()
        return f"AssertionError during tests:\n{tb}"
    except Exception as e:
        tb = traceback.format_exc()
        return f"Error executing tests: {type(e).__name__}: {e}\n{tb}"


@mcp.tool()
def inspect_task() -> str:
    """Return details of the current MBPP task."""
    task = _load_current_task()
    if not task:
        return "No task currently loaded."

    return (
        f"Task ID: {task.get('task_id')}\n"
        f"Definition: {task.get('task_definition')}\n"
        f"Function: {task.get('function_definition')}\n"
        f"Public Tests:\n" + "\n".join(task.get("test_list", []))
    )


# ---------------------------------------------------------------------------
# Resources and Prompts
# ---------------------------------------------------------------------------

@mcp.resource("mbpp://task")
def task_resource() -> str:
    """Current MBPP task resource."""
    return inspect_task()


@mcp.prompt("solve-mbpp")
def solve_prompt(task_definition: str) -> str:
    """Prompt template for solving an MBPP problem."""
    return (
        f"Implement a Python function to solve:\n{task_definition}\n"
        "Test your code using test_code(code) and when all tests pass, "
        "call final_answer(solution_code)."
    )


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    transport = "stdio"
    port = 8001

    if "--transport" in sys.argv:
        t_idx = sys.argv.index("--transport")
        if t_idx + 1 < len(sys.argv):
            transport = sys.argv[t_idx + 1]

    if "--port" in sys.argv:
        p_idx = sys.argv.index("--port")
        if p_idx + 1 < len(sys.argv):
            port = int(sys.argv[p_idx + 1])

    if transport == "sse":
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
