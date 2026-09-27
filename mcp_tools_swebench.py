"""SWE-bench MCP Server exposing mandatory tools, resources, and prompts."""
import ast
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("swebench-tools")

# Working directory / testbed root
TESTBED_DIR = os.environ.get("TESTBED_PATH", os.getcwd())
EVAL_SCRIPT_PATH = os.environ.get("EVAL_SCRIPT_PATH", "")


def _resolve_path(filepath: str) -> Path:
    """Resolve file path relative to TESTBED_DIR or as absolute path."""
    p = Path(filepath)
    if not p.is_absolute():
        p = Path(TESTBED_DIR) / p
    return p.resolve()


# ---------------------------------------------------------------------------
# File System Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def read_file(filepath: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Read the content of a file with line numbers.

    The output format is similar to cat -n:
    <line_number>: <line_content>
    """
    path = _resolve_path(filepath)
    if not path.is_file():
        return f"Error: File not found: {filepath}"

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"Error reading file {filepath}: {e}"

    total_lines = len(lines)
    s = 1 if start_line is None else max(1, int(start_line))
    e = total_lines if end_line is None else min(total_lines, int(end_line))

    if s > total_lines:
        return f"Error: start_line {s} exceeds total lines {total_lines}"
    if s > e:
        return f"Error: start_line {s} is greater than end_line {e}"

    output = []
    for idx in range(s, e + 1):
        content = lines[idx - 1].rstrip("\r\n")
        output.append(f"{idx}: {content}")

    return "\n".join(output)


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    """Replace an exact string in a file with a new string.

    old_str must occur exactly once in the file to prevent unintended modifications.
    """
    path = _resolve_path(filepath)
    if not path.is_file():
        return f"Error: File not found: {filepath}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file {filepath}: {e}"

    occurrences = content.count(old_str)
    if occurrences == 0:
        return f"Error: old_str not found in {filepath}"
    if occurrences > 1:
        return f"Error: old_str occurs {occurrences} times in {filepath}. Please provide more surrounding context to make it unique."

    new_content = content.replace(old_str, new_str, 1)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return f"Error writing to file {filepath}: {e}"

    # Syntax verification for Python files
    feedback = f"Successfully edited {filepath}."
    if path.suffix == ".py":
        try:
            ast.parse(new_content, filename=str(path))
        except SyntaxError as syn_err:
            feedback += f"\nWARNING: Edit introduced a syntax error: {syn_err.msg} at line {syn_err.lineno}: {syn_err.text}"

    return feedback


@mcp.tool()
def list_files(directory: str = ".", pattern: str = "*") -> str:
    """List files in a directory matching a given pattern."""
    target_dir = _resolve_path(directory)
    if not target_dir.is_dir():
        return f"Error: Directory not found: {directory}"

    matched_files = []
    try:
        for root, dirs, files in os.walk(target_dir):
            # Ignore git and cache folders
            dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__", ".pytest_cache"}]
            for filename in files:
                if fnmatch.fnmatch(filename, pattern):
                    full_p = Path(root) / filename
                    matched_files.append(str(full_p))
    except Exception as e:
        return f"Error listing directory {directory}: {e}"

    matched_files.sort()
    if not matched_files:
        return f"No files matched pattern '{pattern}' in '{directory}'."

    return "\n".join(matched_files)


# ---------------------------------------------------------------------------
# Code Search Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_code(pattern: str, file_pattern: str = "*") -> str:
    """Perform a grep-like search in the codebase.

    The output format is:
    /absolute/path_to_file.py:<line_number> <line_content>
    """
    results = []
    base_dir = Path(TESTBED_DIR).resolve()

    try:
        compiled_re = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"

    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__", ".pytest_cache"}]
        for filename in files:
            if fnmatch.fnmatch(filename, file_pattern):
                full_path = Path(root) / filename
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, start=1):
                            if compiled_re.search(line):
                                clean_line = line.rstrip("\r\n")
                                results.append(f"{full_path}:{line_num} {clean_line}")
                except Exception:
                    continue

    if not results:
        return f"No matches found for pattern '{pattern}'."

    return "\n".join(results[:200])


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    """Find the definition of a function or a class.

    The output format is similar to search_code:
    /absolute/path_to_file.py:<line_number> <line_content>
    """
    results = []
    base_dir = Path(TESTBED_DIR).resolve()
    # Pattern to match def or class statements
    regex_pattern = re.compile(rf"^\s*(def|class)\s+{re.escape(name)}\b")

    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__", ".pytest_cache"}]
        for filename in files:
            if filename.endswith(".py"):
                full_path = Path(root) / filename
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, start=1):
                            if regex_pattern.search(line):
                                clean_line = line.rstrip("\r\n")
                                results.append(f"{full_path}:{line_num} {clean_line}")
                except Exception:
                    continue

    if not results:
        return f"No definition found for symbol '{name}'."

    return "\n".join(results[:100])


@mcp.tool()
def find_references(name: str, filepath: Optional[str] = None, line: Optional[int] = None) -> str:
    """Find all usages of a symbol (function or class).

    The output format is similar to search_code:
    /absolute/path_to_file.py:<line_number> <line_content>
    """
    ref_pattern = rf"\b{re.escape(name)}\b"
    return search_code(ref_pattern, file_pattern="*.py")


# ---------------------------------------------------------------------------
# Execution Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def run_tests() -> str:
    """Execute the evaluation script or pytest."""
    if EVAL_SCRIPT_PATH and os.path.isfile(EVAL_SCRIPT_PATH):
        cmd = f"bash {EVAL_SCRIPT_PATH}"
    elif os.path.isfile(os.path.join(TESTBED_DIR, "eval.sh")):
        cmd = f"bash {os.path.join(TESTBED_DIR, 'eval.sh')}"
    else:
        cmd = "pytest"

    return run_command(command=cmd, workdir=TESTBED_DIR)


@mcp.tool()
def get_patch() -> str:
    """Retrieve the unified git diff of all changes made to the repository."""
    cmd = "git -c core.fileMode=false diff"
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=TESTBED_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        diff_out = proc.stdout
        if proc.returncode != 0 and proc.stderr:
            return f"Error getting patch: {proc.stderr}"
        return diff_out
    except Exception as e:
        return f"Error retrieving git patch: {e}"


@mcp.tool()
def run_command(command: str, workdir: Optional[str] = None) -> str:
    """Execute a shell command in the specified working directory.

    Returns the command's stdout, stderr, and exit code.
    """
    target_cwd = workdir or TESTBED_DIR
    target_cwd = str(_resolve_path(target_cwd))

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=target_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        return (
            f"Exit Code: {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after 60 seconds: {command}"
    except Exception as e:
        return f"Error executing command: {e}"


# ---------------------------------------------------------------------------
# Resources and Prompts
# ---------------------------------------------------------------------------

@mcp.resource("swebench://status")
def repo_status() -> str:
    """Current repository status and git diff summary."""
    patch = get_patch()
    return f"Testbed: {TESTBED_DIR}\nCurrent Patch Length: {len(patch)} chars"


@mcp.prompt("debug-issue")
def debug_prompt(issue_description: str) -> str:
    """Prompt template for tackling a SWE-bench issue."""
    return (
        f"You are debugging the following issue:\n{issue_description}\n"
        "Start by locating relevant functions using search_function_or_class_definition_in_code, "
        "read the implementation with read_file, form a fix hypothesis, edit the file with edit_file, "
        "run tests with run_tests(), and submit final_answer(get_patch())."
    )


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    transport = "stdio"
    port = 8000

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
