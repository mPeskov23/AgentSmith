"""SWE-bench Agent CLI for navigating codebases and generating git bug-fix patches."""
import argparse
import json
import os
import sys
from pathlib import Path

from llm_client import LLMClient
from mcp_client import McpClientManager
from models import SandboxConfig, SolutionOutput, SWEBenchTaskInput
from orchestrator import AgentOrchestrator
from sandbox import ExecutionSandbox


def solve_swebench(
    task_file: str,
    output_file: str,
    model_name: str = "qwen/qwen-2.5-coder-32b-instruct",
    provider_url: str = "https://openrouter.ai/api/v1",
    max_iterations: int = 30,
    max_input_tokens: int = 300000,
    max_output_tokens: int = 10000,
    max_time_seconds: float = 900.0,
) -> SolutionOutput:
    """Run autonomous SWE-bench agent on the given issue/repo."""
    # 1. Load task input
    with open(task_file, "r", encoding="utf-8") as f:
        task_dict = json.load(f)

    task_input = SWEBenchTaskInput.model_validate(task_dict)

    # Set environment variables for SWE-bench MCP tools
    testbed_dir = os.environ.get("TESTBED_PATH", "/testbed")
    if not os.path.exists(testbed_dir):
        testbed_dir = os.getcwd()

    os.environ["TESTBED_PATH"] = testbed_dir
    if task_input.eval_script:
        eval_path = Path("/tmp/agent/eval.sh")
        try:
            eval_path.parent.mkdir(parents=True, exist_ok=True)
            with open(eval_path, "w", encoding="utf-8") as ef:
                ef.write(task_input.eval_script)
            os.environ["EVAL_SCRIPT_PATH"] = str(eval_path)
        except Exception:
            pass

    # 2. Start MCP server for SWE-bench via stdio
    script_dir = Path(__file__).parent.resolve()
    swe_tool_script = script_dir / "mcp_tools_swebench.py"
    if not swe_tool_script.exists():
        swe_tool_script = Path("mcp_tools_swebench.py").resolve()

    mcp_client = McpClientManager(
        stdio_cmd=[sys.executable, str(swe_tool_script)],
        timeout=60.0,
    )

    # 3. Configure Sandbox
    allowed_imports = [
        "math", "math.*", "collections", "collections.*", "itertools",
        "re", "json", "typing", "typing.*", "functools", "operator",
        "heapq", "bisect", "copy", "string", "random", "datetime",
        "datetime.*", "array", "cmath",
    ]

    sandbox_config = SandboxConfig(
        authorized_imports=allowed_imports,
        allowed_directories=["/testbed", "/tmp/agent", str(script_dir), testbed_dir],
        max_execution_time_seconds=30,
        max_memory_mb=512,
    )

    sandbox = ExecutionSandbox(config=sandbox_config, mcp_client=mcp_client)

    # 4. Initialize LLM Client
    llm_client = LLMClient(
        model_name=model_name,
        provider_url=provider_url,
    )

    # 5. Build task description
    hints = f"\nHints:\n{task_input.hints_text}" if task_input.hints_text else ""
    task_desc = (
        f"Instance ID: {task_input.instance_id}\n"
        f"Repository: {task_input.repo}\n"
        f"Problem Statement:\n{task_input.problem_statement}\n{hints}\n"
        f"Instructions: Locate the relevant code files using search_code or search_function_or_class_definition_in_code. "
        f"Inspect the context using read_file. Modify the code to resolve the issue using edit_file. "
        f"Test your changes using run_tests(). When the bug is fixed, submit your patch by calling:\n"
        f"final_answer(get_patch())"
    )

    # Mock support for deterministic evaluation & tests
    mock_steps = None
    if provider_url == "mock" or os.environ.get("AGENT_MOCK_LLM") == "1":
        # Check if this is a known task with a reference patch for testing
        step1 = (
            "Thought: I will search for relevant definitions to locate the issue.\n"
            "```python\n"
            "files = list_files('.', '*.py')\n"
            "print('Files found:', len(files.split('\\n')))\n"
            "```"
        )
        step2 = (
            "Thought: Now retrieving the git patch.\n"
            "```python\n"
            "patch = get_patch()\n"
            "final_answer(patch if patch else 'diff --git a/file.py b/file.py\\n--- a/file.py\\n+++ b/file.py\\n@@ -1 +1 @@\\n-old\\n+new\\n')\n"
            "```"
        )
        mock_steps = [step1, step2]

    # 6. Run Orchestrator
    orchestrator = AgentOrchestrator(
        task_id=task_input.instance_id,
        benchmark="swebench",
        task_description=task_desc,
        sandbox=sandbox,
        llm_client=llm_client,
        max_iterations=max_iterations,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_time_seconds=max_time_seconds,
        mock_steps=mock_steps,
    )

    try:
        solution_output = orchestrator.run()
    finally:
        sandbox.close()

    # 7. Write solution output
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(solution_output.model_dump_json(indent=2))

    print(f"Solution written to: {output_file}")
    return solution_output


def main():
    """CLI entry point for agent_swebench."""
    parser = argparse.ArgumentParser(description="Agent Smith SWE-bench Runner")
    parser.add_argument("--task-file", required=True, help="Path to dumped task.json")
    parser.add_argument("--output", required=True, help="Path to write solution.json")
    parser.add_argument("--model-name", default="qwen/qwen-2.5-coder-32b-instruct", help="Model name")
    parser.add_argument("--provider-url", default="https://openrouter.ai/api/v1", help="Provider API base URL")
    parser.add_argument("--max-iterations", type=int, default=30, help="Max iterations (default 30)")
    parser.add_argument("--max-input-tokens", type=int, default=300000, help="Max input tokens (default 300000)")
    parser.add_argument("--max-output-tokens", type=int, default=10000, help="Max output tokens (default 10000)")
    parser.add_argument("--timeout", type=float, default=900.0, help="Max execution timeout in seconds")

    args = parser.parse_args()

    solve_swebench(
        task_file=args.task_file,
        output_file=args.output,
        model_name=args.model_name,
        provider_url=args.provider_url,
        max_iterations=args.max_iterations,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        max_time_seconds=args.timeout,
    )


if __name__ == "__main__":
    main()
