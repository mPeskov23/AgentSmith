*This project has been created as part of the 42 curriculum by mikhail.*

# Agent Smith: Autonomous Reasoning, Code Generation, and Execution

## Description
Agent Smith is a modular, extensible, and secure autonomous agentic framework designed to solve challenging software engineering tasks (such as SWE-bench bug fixing in production codebases) and algorithmic problems (MBPP). Rather than relying on simple one-shot prompt completion or static JSON tool-calling schemas, Agent Smith implements an executable Python-driven loop: **Thought → Code → Observation**.

The agent reasons about problems, writes executable Python code that invokes Model Context Protocol (MCP) tools, runs the code inside a hardened sandbox adhering to strict security limitations, observes execution results, and iterates autonomously until reaching a validated solution.

---

## Instructions

### Requirements & Installation
- Python 3.10
- [uv](https://docs.astral.sh/uv/) package manager

To set up the environment and install dependencies:
```bash
# Sync dependencies using uv
uv sync
```

### Sandbox Usage
The secure sandbox can be run in standalone interactive REPL mode or connected to MCP servers:
```bash
# Launch interactive sandbox REPL
uv run sandbox

# Launch sandbox with custom configuration
uv run sandbox sandbox_template.json

# Launch sandbox with MBPP tools (stdio transport)
uv run sandbox --mcp-stdio "python mcp_tools_mbpp.py" sandbox_template.json

# Launch sandbox with SWE-bench tools (stdio transport)
uv run sandbox --mcp-stdio "python mcp_tools_swebench.py" sandbox_template.json

# Launch sandbox with remote MCP server (HTTP/SSE transport)
uv run sandbox --mcp-server http://localhost:8000/sse sandbox_template.json
```

### Running the Agents
```bash
# Run MBPP Agent
uv run python -m agent_mbpp --task-file cache/mbpp_task.json \
  --output cache/mbpp_solution.json \
  --model-name "qwen/qwen-2.5-coder-32b-instruct" \
  --provider-url "https://openrouter.ai/api/v1"

# Run SWE-bench Agent
uv run python -m agent_swebench --task-file cache/swebench_task.json \
  --output cache/swebench_solution.json \
  --model-name "qwen/qwen-2.5-coder-32b-instruct" \
  --provider-url "https://openrouter.ai/api/v1"
```

### Running Evaluation Exams
```bash
# Run MBPP Exam (5 tasks, 4/5 pass threshold)
./exam_mbpp.sh --student-path . --moulinette-path ./moulinette --env-file .env

# Run SWE-bench Exam (3 tasks, 2/3 pass threshold)
./exam_swebench.sh --student-path . --moulinette-path ./moulinette --env-file .env

# Run Sandbox Security Exam
./exam_sandbox.sh --student-path . --moulinette-path ./moulinette --env-file .env
```

---

## System Architecture

The architecture consists of decoupled layers:

```
┌────────────────────────────────────────────────────────┐
│                   Agent Smith CLI                      │
│     (uv run sandbox | agent_mbpp | agent_swebench)     │
└───────────────────────────┬────────────────────────────┘
                            │
              ┌─────────────▼─────────────┐
              │    Agent Orchestrator     │
              │  (Thought → Code → Obs)   │
              │  - Format Normalization   │
              │  - Limits & Token Guard   │
              └──────┬─────────────┬──────┘
                     │             │
       ┌─────────────▼────┐   ┌────▼────────────────────────┐
       │   LLM Service    │   │  Secure Execution Sandbox   │
       │ - Key Rotation   │   │ - Pure stdlib security      │
       │ - Fallback & Mock│   │ - Injected final_answer()   │
       └──────────────────┘   └─────────────┬───────────────┘
                                            │
                               ┌────────────▼───────────────┐
                               │     MCP Client Bridge      │
                               └────────────┬───────────────┘
                                            │ stdio / SSE
                               ┌────────────▼───────────────┐
                               │        MCP Servers         │
                               │  - mcp_tools_swebench.py   │
                               │  - mcp_tools_mbpp.py       │
                               └────────────────────────────┘
```

---

## Agent Loop Explanation
The core agent loop runs autonomously through consecutive cycles:
1. **Thought**: The LLM analyzes the current goal, past steps, tool outputs, and forms a hypothesis.
2. **Code**: The model emits Python code (or tool calls in XML, Hermes JSON, or ReAct format, which the extraction layer normalizes into Python statements).
3. **Execution & Observation**: The code executes inside the sandbox. Return values and stdout are captured and fed back to the LLM as the next observation.
4. **Termination**: When the model calls `final_answer(solution)`, the sandbox catches the signal, records the solution, and safely terminates the loop, outputting `SolutionOutput`. If iteration, token, or time limits are reached, the agent halts gracefully.

---

## Sandbox Design
The sandbox acts as a security boundary separating untrusted model-generated code from the host machine:
- **Import Allowlist**: Enforces `authorized_imports` (supporting wildcard matching such as `math.*`). Unapproved imports raise `ImportError`.
- **Filesystem Restrictions**: Enforces `allowed_directories`. Paths are canonicalized to prevent traversal attacks. Unauthorized paths raise `PermissionError`.
- **Network Block**: Socket creations are blocked, preventing any network egress or ingress.
- **Execution Timeout**: Uses Unix `SIGALRM` to terminate runaway execution.
- **Memory Limit**: Restricts virtual memory using `resource.setrlimit`.
- **Pure Standard Library**: Security mechanisms use exclusively standard Python builtins and libraries without external unsafe C extensions.

---

## Tool Implementation Details
Mandatory MCP tools are implemented in standalone servers:
- **`read_file(filepath, start_line, end_line)`**: Reads file contents with line numbering (`<line_number>: <line_content>`).
- **`edit_file(filepath, old_str, new_str)`**: Replaces unique string occurrences and validates Python syntax against lint errors.
- **`list_files(directory, pattern)`**: Recursively lists files matching patterns.
- **`search_code(pattern, file_pattern)`**: Regex/grep search returning `/path:<line> <content>`.
- **`search_function_or_class_definition_in_code(name)`**: AST-based discovery of function and class definitions.
- **`find_references(name, filepath, line)`**: Symbol reference locator across the repository.
- **`run_tests()`**: Executes task evaluation tests.
- **`get_patch()`**: Returns clean git diff via `git -c core.fileMode=false diff`.
- **`run_command(command, workdir)`**: Safely executes shell commands in a given directory.

---

## Benchmark Results and Analysis
For detailed evaluation and model comparison across SWE-bench tasks, refer to [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md).

---

## Resources & AI Usage
- Model Context Protocol (MCP) Specification: https://modelcontextprotocol.io
- SWE-bench Verified Dataset & Evaluation Harness: https://www.swebench.com
- Python `resource`, `signal`, and `importlib` Standard Library documentation.

### AI Usage Disclosure
AI was used during development as an engineering pair programmer to assist in drafting test suites, reviewing edge cases for import interception, and synthesizing benchmark logs. All architectural decisions, sandbox isolation guarantees, and tool implementations were designed and verified under human direction.
