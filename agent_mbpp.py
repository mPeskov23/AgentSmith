"""MBPP Agent CLI for solving algorithmic Python programming challenges."""
import argparse
import json
import os
import sys
from pathlib import Path

from llm_client import LLMClient
from mcp_client import McpClientManager
from models import MBPPTaskInput, SandboxConfig, SolutionOutput
from orchestrator import AgentOrchestrator
from sandbox import ExecutionSandbox


def solve_mbpp(
    task_file: str,
    output_file: str,
    model_name: str = "qwen/qwen-2.5-coder-32b-instruct",
    provider_url: str = "https://openrouter.ai/api/v1",
    max_iterations: int = 10,
    max_input_tokens: int = 4000,
    max_output_tokens: int = 1000,
    max_time_seconds: float = 60.0,
) -> SolutionOutput:
    """Run autonomous MBPP agent on the given task."""
    # 1. Load task input
    with open(task_file, "r", encoding="utf-8") as f:
        task_dict = json.load(f)

    task_input = MBPPTaskInput.model_validate(task_dict)

    # Export task path for MBPP MCP tools
    abs_task_file = str(Path(task_file).resolve())
    os.environ["MBPP_TASK_FILE"] = abs_task_file

    # 2. Start MCP server for MBPP via stdio
    script_dir = Path(__file__).parent.resolve()
    mbpp_tool_script = script_dir / "mcp_tools_mbpp.py"
    if not mbpp_tool_script.exists():
        mbpp_tool_script = Path("mcp_tools_mbpp.py").resolve()

    mcp_client = McpClientManager(
        stdio_cmd=[sys.executable, str(mbpp_tool_script)],
        timeout=30.0,
    )

    # 3. Configure Sandbox
    # Merge default authorized imports with any test imports required by task
    allowed_imports = [
        "math", "math.*", "collections", "collections.*", "itertools",
        "re", "json", "typing", "typing.*", "functools", "operator",
        "heapq", "bisect", "copy", "string", "random", "datetime",
        "datetime.*", "array", "cmath",
    ]
    for imp in task_input.test_imports:
        clean_imp = imp.replace("import ", "").replace("from ", "").split()[0].split(".")[0]
        if clean_imp and clean_imp not in allowed_imports:
            allowed_imports.append(clean_imp)

    sandbox_config = SandboxConfig(
        authorized_imports=allowed_imports,
        allowed_directories=[str(script_dir), "/tmp/agent"],
        max_execution_time_seconds=20,
        max_memory_mb=512,
    )

    sandbox = ExecutionSandbox(config=sandbox_config, mcp_client=mcp_client)

    # 4. Initialize LLM Client
    llm_client = LLMClient(
        model_name=model_name,
        provider_url=provider_url,
    )

    # 5. Build task description
    test_str = "\n".join(task_input.test_list)
    imports_str = "\n".join(task_input.test_imports)
    task_desc = (
        f"Task ID: {task_input.task_id}\n"
        f"Description: {task_input.task_definition}\n"
        f"Function Signature: {task_input.function_definition}\n"
        f"Required Imports:\n{imports_str}\n"
        f"Public Assertions:\n{test_str}\n\n"
        f"Instructions: Write a Python function that implements the requested functionality and satisfies all assertions. "
        f"Test it thoroughly using test_code(code). When it passes all assertions, submit your final answer by calling:\n"
        f"final_answer(your_solution_code)"
    )

    # Check for offline mock mode
    mock_steps = None
    if provider_url == "mock" or os.environ.get("AGENT_MOCK_LLM") == "1":
        # Generate mock solution tailored for this task
        # If task has a known function definition, create a working mock
        func_sig = task_input.function_definition.strip()
        func_name = func_sig.replace("def ", "").split("(")[0].strip()

        # Build mock code block that returns correct answer for common assertions
        mock_code = f"{func_sig}\n    pass\n"
        if "similar_elements" in func_name:
            mock_code = "def similar_elements(test_tup1, test_tup2):\n    return tuple(set(test_tup1) & set(test_tup2))\n"
        elif "sub_list" in func_name:
            mock_code = "def sub_list(nums1, nums2):\n    return list(map(lambda x, y: x - y, nums1, nums2))\n"

        step1 = (
            f"Thought: I need to implement {func_name}. Let's test the implementation.\n"
            f"```python\n"
            f"code = '''{mock_code}'''\n"
            f"result = test_code(code)\n"
            f"print(result)\n"
            f"```"
        )
        step2 = (
            f"Thought: All tests passed. I will now submit the final answer.\n"
            f"```python\n"
            f"final_answer('''{mock_code}''')\n"
            f"```"
        )
        mock_steps = [step1, step2]

    # 6. Run Orchestrator
    orchestrator = AgentOrchestrator(
        task_id=str(task_input.task_id),
        benchmark="mbpp",
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
    """CLI entry point for agent_mbpp."""
    parser = argparse.ArgumentParser(description="Agent Smith MBPP Runner")
    parser.add_argument("--task-file", required=True, help="Path to dumped task.json")
    parser.add_argument("--output", required=True, help="Path to write solution.json")
    parser.add_argument("--model-name", default="qwen/qwen-2.5-coder-32b-instruct", help="Model name")
    parser.add_argument("--provider-url", default="https://openrouter.ai/api/v1", help="Provider API base URL")
    parser.add_argument("--max-iterations", type=int, default=10, help="Max iterations (default 10)")
    parser.add_argument("--max-input-tokens", type=int, default=4000, help="Max input tokens (default 4000)")
    parser.add_argument("--max-output-tokens", type=int, default=1000, help="Max output tokens (default 1000)")
    parser.add_argument("--timeout", type=float, default=60.0, help="Max execution timeout in seconds")

    args = parser.parse_args()

    solve_mbpp(
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
