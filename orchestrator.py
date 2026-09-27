"""Agent Smith Orchestrator implementing Thought -> Code -> Observation loop."""
import time
from typing import List, Optional

from code_extractor import extract_code
from llm_client import LLMClient
from models import SolutionOutput, StepMetrics
from sandbox import ExecutionSandbox


def build_system_prompt(benchmark: str, sandbox_manual: str) -> str:
    """Construct a comprehensive system prompt with clear documentation and problem-solving methodology."""
    if benchmark == "mbpp":
        task_instruction = (
            "You are an expert Python software engineer solving algorithmic programming tasks.\n"
            "Your goal is to write a correct, robust Python function matching the task description and function signature.\n"
            "You must test your solution using test_code(code) or run_tests() before submitting.\n"
            "When all tests pass, call final_answer(solution_code) with your complete Python function code string.\n"
        )
    else:
        task_instruction = (
            "You are an expert software engineer resolving real-world repository issues in SWE-bench.\n"
            "Your goal is to inspect the codebase, locate the bug, formulate a fix, apply it with edit_file, "
            "verify the fix using run_tests(), and submit final_answer(get_patch()).\n"
        )

    return f"""{task_instruction}
### Operational Protocol: Thought -> Code -> Observation
Operate through structured cycles:
1. **Thought**: Explain your reasoning, analyze previous observations, and describe your concrete plan for this step.
2. **Code**: Provide executable Python code enclosed in a ```python ... ``` block. Use the available tools to explore, edit, or test.
3. **Observation**: You will receive the output of your code execution from the sandbox environment.

### Rules & Best Practices:
- Call tools by executing Python code. Do not hallucinate or invent tool outputs.
- Keep exploration focused. Search for symbols and read only relevant lines.
- Always verify your modifications with tests before submitting.
- When the task is solved, you MUST call final_answer(...) to complete the task.

{sandbox_manual}
"""


class AgentOrchestrator:
    """Manages the agent loop, enforce limits, records StepMetrics, and produces SolutionOutput."""

    def __init__(
        self,
        task_id: str,
        benchmark: str,
        task_description: str,
        sandbox: ExecutionSandbox,
        llm_client: LLMClient,
        max_iterations: int = 10,
        max_input_tokens: int = 6000,
        max_output_tokens: int = 1500,
        max_time_seconds: float = 120.0,
        mock_steps: Optional[List[str]] = None,
    ):
        self.task_id = task_id
        self.benchmark = benchmark
        self.task_description = task_description
        self.sandbox = sandbox
        self.llm_client = llm_client
        self.max_iterations = max_iterations
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.max_time_seconds = max_time_seconds
        self.mock_steps = mock_steps or []

    def run(self) -> SolutionOutput:
        """Execute the agentic loop until completion or limit exhaustion."""
        start_time = time.perf_counter()
        system_prompt = build_system_prompt(self.benchmark, self.sandbox.get_manual())

        messages = [
            {"role": "user", "content": f"Task Description:\n{self.task_description}\n\nPlease begin."}
        ]

        steps: List[StepMetrics] = []
        total_requests = 0
        total_input_tokens = 0
        total_output_tokens = 0
        current_error: Optional[str] = None
        solved_solution = ""

        for step_idx in range(1, self.max_iterations + 1):
            elapsed_time = time.perf_counter() - start_time

            # Hard limits checks
            if elapsed_time > self.max_time_seconds:
                current_error = f"Hard limit exceeded: Execution timeout ({elapsed_time:.1f}s > {self.max_time_seconds}s)"
                break

            if total_input_tokens >= self.max_input_tokens:
                current_error = f"Hard limit exceeded: Maximum input tokens reached ({total_input_tokens} >= {self.max_input_tokens})"
                break

            if total_output_tokens >= self.max_output_tokens:
                current_error = f"Hard limit exceeded: Maximum output tokens reached ({total_output_tokens} >= {self.max_output_tokens})"
                break

            # Obtain mock response if provided for deterministic testing
            mock_resp = None
            if self.mock_steps and step_idx <= len(self.mock_steps):
                mock_resp = self.mock_steps[step_idx - 1]

            # LLM generation
            try:
                llm_resp = self.llm_client.generate(
                    system_prompt=system_prompt,
                    messages=messages,
                    stop_sequences=["```\n\n", "<end_code>"],
                    mock_response=mock_resp,
                )
            except Exception as e:
                current_error = f"LLM API Failure: {e}"
                break

            total_requests += 1 + llm_resp.retries
            total_input_tokens += llm_resp.input_tokens
            total_output_tokens += llm_resp.output_tokens

            # Code extraction
            code, feedback = extract_code(llm_resp.content)
            sandbox_input = code or ""
            sandbox_output = ""

            if code is None:
                sandbox_output = feedback or "No valid code block was found in the model's response."
                messages.append({"role": "assistant", "content": llm_resp.content})
                messages.append({"role": "user", "content": f"Observation:\n{sandbox_output}"})
            else:
                # Execute inside sandbox
                sandbox_output = self.sandbox.execute(code)
                if feedback:
                    sandbox_output = f"Feedback: {feedback}\n\nExecution Result:\n{sandbox_output}"

                messages.append({"role": "assistant", "content": llm_resp.content})
                messages.append({"role": "user", "content": f"Observation:\n{sandbox_output}"})

            # Record step metrics
            metric = StepMetrics(
                step=step_idx,
                input_tokens=llm_resp.input_tokens,
                output_tokens=llm_resp.output_tokens,
                request_time_ms=llm_resp.latency_ms,
                api_url=llm_resp.api_url,
                model_name=llm_resp.model_name,
                llm_output=llm_resp.content,
                sandbox_input=sandbox_input,
                sandbox_output=sandbox_output,
                retries=llm_resp.retries,
            )
            steps.append(metric)

            # Check for final answer completion
            if self.sandbox.has_finished:
                solved_solution = self.sandbox.final_answer or ""
                break

        total_elapsed = time.perf_counter() - start_time
        success = self.sandbox.has_finished and current_error is None

        if not self.sandbox.has_finished and not current_error:
            current_error = f"Maximum iterations reached ({self.max_iterations}) without final_answer."

        return SolutionOutput(
            task_id=str(self.task_id),
            benchmark=self.benchmark,
            success=success,
            solution=solved_solution,
            iterations=len(steps),
            total_requests=total_requests,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_time_seconds=total_elapsed,
            steps=steps,
            system_prompt=system_prompt,
            error=current_error,
        )
