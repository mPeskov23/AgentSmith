"""Extraction layer for normalizing LLM outputs into executable Python code blocks."""
import ast
import json
import re
import xml.etree.ElementTree as ET
from typing import Optional, Tuple


def _args_to_py_call(func_name: str, args_dict: dict) -> str:
    """Format a function name and arguments dictionary into a Python call string."""
    args_strs = []
    for k, v in args_dict.items():
        args_strs.append(f"{k}={repr(v)}")
    return f"{func_name}({', '.join(args_strs)})"


def extract_code(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract and normalize Python code from arbitrary model output formats.

    Returns:
        (extracted_python_code, feedback_message)
        If no valid code block is found, returns (None, error_feedback).
        If code is malformed but recovered, returns (code, explanation_feedback).
        If clean code block is extracted, returns (code, None).
    """
    if not text or not text.strip():
        return None, "No valid code block was found in the model's response: Response was empty."

    # 1. Primary: Python markdown code blocks ```python ... ``` or ``` ... ```
    py_block_match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if py_block_match:
        code = py_block_match.group(1).strip()
        if code:
            return code, None

    # Check for <end_code> delimiter
    end_code_match = re.search(r"```(?:python)?\s*\n(.*?)(?:<end_code>|\Z)", text, re.DOTALL | re.IGNORECASE)
    if "<end_code>" in text:
        parts = text.split("<end_code>")[0]
        if "```" in parts:
            code = parts.split("```")[-1].replace("python", "", 1).strip()
            if code:
                return code, None

    # Check unclosed code block (e.g. hit max generation tokens or omitted closing backticks)
    unclosed_match = re.search(r"```(?:python)?\s*\n(.*)\Z", text, re.DOTALL | re.IGNORECASE)
    if unclosed_match:
        code = unclosed_match.group(1).strip()
        if code:
            return (
                code,
                "A code block was malformed but was interpreted anyway "
                "(missing closing triple-backticks ``` delimiter).",
            )

    # 2. JSON / Hermes tool calls: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    hermes_matches = re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
    if hermes_matches:
        calls = []
        for h_json in hermes_matches:
            try:
                data = json.loads(h_json)
                func_name = data.get("name")
                args = data.get("arguments", {})
                if func_name:
                    calls.append(_args_to_py_call(func_name, args))
            except Exception:
                continue
        if calls:
            return "\n".join(calls), "Converted JSON/Hermes <tool_call> block to Python function call."

    # 3. Anthropic XML tool calls: <invoke name="..."><parameter name="p">v</parameter></invoke>
    if "<invoke" in text:
        try:
            calls = []
            for inv_block in re.findall(r"<invoke\s+name=[\"'](.*?)[\"']\s*>(.*?)</invoke>", text, re.DOTALL):
                func_name = inv_block[0]
                body = inv_block[1]
                args = {}
                for param in re.findall(r"<parameter\s+name=[\"'](.*?)[\"']\s*>(.*?)</parameter>", body, re.DOTALL):
                    args[param[0]] = param[1].strip()
                calls.append(_args_to_py_call(func_name, args))
            if calls:
                return "\n".join(calls), "Converted XML Anthropic-style tool call to Python function call."
        except Exception:
            pass

    # 4. ReAct format: Action: tool_name \n Action Input: {...}
    react_action = re.search(r"Action:\s*([a-zA-Z0-9_]+)", text)
    if react_action:
        tool_name = react_action.group(1).strip()
        input_match = re.search(r"Action Input:\s*(\{.*?\}|\[.*?\]|\".*?\"|'.*?'|[^\n]+)", text, re.DOTALL)
        args_dict = {}
        if input_match:
            raw_input = input_match.group(1).strip()
            try:
                args_dict = json.loads(raw_input)
                if not isinstance(args_dict, dict):
                    args_dict = {"input": args_dict}
            except Exception:
                args_dict = {"input": raw_input}

        py_call = _args_to_py_call(tool_name, args_dict)
        return py_call, "Converted ReAct Action/Action Input format to Python function call."

    # 5. Direct Python statement / call fallback (e.g. final_answer("...") without backticks)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    candidate_lines = []
    for line in lines:
        if any(line.startswith(prefix) for prefix in [
            "final_answer(", "read_file(", "edit_file(", "search_code(", "list_files(",
            "run_tests(", "get_patch(", "run_command(", "test_code("
        ]):
            candidate_lines.append(line)

    if candidate_lines:
        code_candidate = "\n".join(candidate_lines)
        try:
            ast.parse(code_candidate)
            return (
                code_candidate,
                "A code block was malformed but was interpreted anyway "
                "(Python calls detected without markdown code block formatting).",
            )
        except SyntaxError:
            pass

    return None, "No valid code block was found in the model's response."
