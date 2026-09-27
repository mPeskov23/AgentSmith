"""Comprehensive security and isolation test suite for Agent Smith sandbox."""
import os
import pytest
from models import SandboxConfig
from sandbox import ExecutionSandbox
from mcp_client import McpClientManager


def test_import_allowed():
    """Verify authorized imports succeed."""
    cfg = SandboxConfig(authorized_imports=["math", "collections", "itertools", "re", "json"])
    s = sandbox = ExecutionSandbox(config=cfg)
    out = s.execute("import math\nres = math.sqrt(64)\nprint(res)")
    assert "8.0" in out


def test_import_blocked():
    """Verify unauthorized imports are blocked."""
    cfg = SandboxConfig(authorized_imports=["math"])
    s = ExecutionSandbox(config=cfg)
    out = s.execute("import subprocess")
    assert "blocked by sandbox security policy" in out.lower()


def test_path_restriction():
    """Verify filesystem access outside allowed_directories is denied."""
    cfg = SandboxConfig(allowed_directories=["/tmp/agent"])
    s = ExecutionSandbox(config=cfg)
    out = s.execute("open('/etc/passwd', 'r')")
    assert "denied by sandbox policy" in out.lower() or "permission" in out.lower()


def test_network_block():
    """Verify network socket creation is blocked."""
    cfg = SandboxConfig(authorized_imports=["socket"])
    s = ExecutionSandbox(config=cfg)
    out = s.execute("import socket\ns = socket.socket()")
    assert "network access is blocked" in out.lower()


def test_timeout_enforcement():
    """Verify execution timeout terminates runaway code."""
    cfg = SandboxConfig(max_execution_time_seconds=1)
    s = ExecutionSandbox(config=cfg)
    out = s.execute("while True: pass")
    assert "timeout" in out.lower()


def test_final_answer_injection():
    """Verify final_answer captures the solution and marks sandbox finished."""
    s = ExecutionSandbox()
    out = s.execute("final_answer('my_solution')")
    assert s.has_finished is True
    assert s.final_answer == "my_solution"
    assert "final answer submitted" in out.lower()


def test_code_extraction():
    """Verify multi-format code extraction layer."""
    from code_extractor import extract_code

    # Python markdown
    c, f = extract_code("```python\nprint(1)\n```")
    assert c == "print(1)"

    # Anthropic XML
    c, f = extract_code('<invoke name="read_file"><parameter name="filepath">a.py</parameter></invoke>')
    assert "read_file" in c and "filepath='a.py'" in c

    # Hermes JSON
    c, f = extract_code('<tool_call>{"name": "list_files", "arguments": {"directory": "."}}</tool_call>')
    assert "list_files" in c and "directory='.'" in c

    # ReAct
    c, f = extract_code('Action: get_patch\nAction Input: {}')
    assert "get_patch" in c


def test_mcp_tools_protocol():
    """Verify MCP tools discovery and execution via stdio."""
    import sys
    client = McpClientManager(stdio_cmd=[sys.executable, "mcp_tools_swebench.py"])
    tool_names = [t.name for t in client.tools]
    assert "read_file" in tool_names
    assert "edit_file" in tool_names
    assert "get_patch" in tool_names
    assert "run_tests" in tool_names
    client.close()
