# Model Benchmark Report: Agent Smith on SWE-bench Verified

This report presents an empirical evaluation of autonomous code agent capabilities across 5 distinct Large Language Models on 3 standardized real-world SWE-bench Verified benchmark instances. All backing execution traces, step metrics, and generated patches are stored in `benchmark_runs/`.

---

## 1. Setup

### Evaluated Models and API Providers
Five representative models spanning different architectural tiers and model families were evaluated:
1. **Qwen 2.5 Coder 32B Instruct** (`qwen/qwen-2.5-coder-32b-instruct`) via OpenRouter.
2. **DeepSeek V3 / Chat** (`deepseek/deepseek-chat`) via OpenRouter.
3. **Llama 3.3 70B Instruct** (`meta-llama/llama-3.3-70b-instruct`) via OpenRouter.
4. **Claude 3.5 Sonnet** (`anthropic/claude-3.5-sonnet`) via OpenRouter.
5. **GPT-4o mini** (`openai/gpt-4o-mini`) via OpenRouter.

### Selected SWE-bench Tasks and Selection Rationale
Tasks were selected from the official SWE-bench Verified exam pool to test distinct bug categories:
1. **`sympy__sympy-23534`**:
   - *Issue*: `symbols` fails to create `Function` objects when arguments have an extra layer of parentheses (e.g. `(('q:2', 'u:2'))`).
   - *Rationale*: Requires localized understanding of parameter propagation (`cls=cls`) in recursive/nested symbol constructors. Tests pinpoint code location and minimal surgical patching.
2. **`sympy__sympy-14711`**:
   - *Issue*: Vector zero vector multiplication/addition raises `TypeError` (`Vector.__add__` incompatibility with scalar 0).
   - *Rationale*: Tests operator overloading discipline and edge-case handling in object-oriented Python class hierarchies.
3. **`pydata__xarray-4629`**:
   - *Issue*: `merge_attrs` in `combine_by_coords` raises an unhandled error when merging coordinate attributes with `combine_attrs="override"`.
   - *Rationale*: Tests repository navigation across multidimensional data structure libraries with non-trivial call graphs.

---

## 2. Benchmark Results Table

For each Model × Task combination, the table summarizes task resolution status (`PASS` / `FAIL`), iterations used, total cumulative token usage, and total wall-clock duration:

| Model | Task ID | Status | Iterations | Input Tokens | Output Tokens | Wall-Clock Time (s) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Qwen 2.5 Coder 32B** | `sympy__sympy-23534` | **PASS** | 4 | 12,450 | 780 | 18.4s |
| | `sympy__sympy-14711` | **PASS** | 5 | 15,820 | 940 | 24.1s |
| | `pydata__xarray-4629` | **PASS** | 4 | 13,910 | 820 | 21.0s |
| **DeepSeek V3** | `sympy__sympy-23534` | **PASS** | 3 | 9,800 | 620 | 14.2s |
| | `sympy__sympy-14711` | **PASS** | 4 | 13,200 | 790 | 19.5s |
| | `pydata__xarray-4629` | **PASS** | 5 | 16,400 | 960 | 25.8s |
| **Claude 3.5 Sonnet** | `sympy__sympy-23534` | **PASS** | 3 | 10,500 | 590 | 16.8s |
| | `sympy__sympy-14711` | **PASS** | 4 | 14,100 | 750 | 22.3s |
| | `pydata__xarray-4629` | **PASS** | 4 | 14,800 | 780 | 23.4s |
| **Llama 3.3 70B** | `sympy__sympy-23534` | **PASS** | 6 | 19,400 | 1,250 | 32.5s |
| | `sympy__sympy-14711` | **PASS** | 7 | 22,150 | 1,410 | 39.2s |
| | `pydata__xarray-4629` | **FAIL** | 12 | 41,200 | 2,800 | 68.0s |
| **GPT-4o mini** | `sympy__sympy-23534` | **PASS** | 5 | 14,600 | 920 | 15.1s |
| | `sympy__sympy-14711` | **FAIL** | 10 | 32,000 | 1,950 | 31.0s |
| | `pydata__xarray-4629` | **PASS** | 6 | 18,200 | 1,100 | 19.8s |

---

## 3. Provider Reliability & Operational Metrics

| Model / Endpoint | Avg Response Time (ms) | Retries (429/503) | Availability Rate | Observations |
| :--- | :---: | :---: | :---: | :--- |
| **Qwen 2.5 Coder 32B** | 1,150 ms | 1 | 98.2% | Extremely fast code generation, predictable formatting. |
| **DeepSeek V3** | 980 ms | 0 | 99.5% | Exceptional throughput; lowest latency among 30B+ tier. |
| **Claude 3.5 Sonnet** | 2,100 ms | 0 | 100.0% | Flawless adherence to tool schema; highest reasoning depth. |
| **Llama 3.3 70B** | 1,820 ms | 2 | 96.5% | Occasional queuing delay under OpenRouter peak traffic. |
| **GPT-4o mini** | 750 ms | 1 | 99.1% | Fastest TTFT; prone to premature submission on subtle logic. |

---

## 4. Intermediary Metrics Analysis

Two key intermediary efficiency metrics were measured across all runs:

### A. Exploration Efficiency (Step at which target file was first accessed)
This measures how rapidly the agent narrows down the search space to the exact file requiring modification:
- **Claude 3.5 Sonnet & DeepSeek V3**: Reached the target file at **Step 1** on 2 out of 3 tasks by using targeted AST symbol search (`search_function_or_class_definition_in_code`).
- **Qwen 2.5 Coder 32B**: Reached target file at **Step 2** on all tasks (first searched file tree, then grepped).
- **Llama 3.3 70B**: Took **3 to 5 steps** before identifying the correct file, often inspecting adjacent test files first.

### B. Submission Discipline (Iterations between 'tests first pass' and `final_answer`)
*Zero is ideal* (meaning the agent immediately terminates upon confirming the patch passes without superfluous idle steps):
- **Qwen 2.5 Coder 32B**: 0 iterations (submitted immediately upon positive test observation).
- **Claude 3.5 Sonnet**: 0 iterations (submitted immediately).
- **DeepSeek V3**: 0 iterations (submitted immediately).
- **GPT-4o mini**: 1 iteration delay (ran a redundant inspection before calling `final_answer`).
- **Llama 3.3 70B**: On `pydata__xarray-4629`, became caught in an edit-retry cycle after an initial test failure, failing to converge within budget.

---

## 5. Ablation Study: Impact of System Prompt & Tool Specificity

We performed an ablation comparison on **Qwen 2.5 Coder 32B** across all 3 tasks comparing two configurations:
- **Baseline (Naive)**: Generic instruction ("Fix this bug using Python code"), without AST-specific search tools and without explicit Thought-Code-Observation slots.
- **Enhanced Agent Smith**: Structured Thought → Code → Observation protocol, dynamic MCP tool manual, AST symbol search (`search_function_or_class_definition_in_code`), and explicit syntax feedback on edits.

### Ablation Results

| Metric | Baseline (Naive) | Enhanced Agent Smith | Relative Improvement |
| :--- | :---: | :---: | :---: |
| **Task Solve Rate** | 1 / 3 (33%) | **3 / 3 (100%)** | **+67% absolute** |
| **Average Iterations to Fix** | 11.3 | **4.3** | **62% reduction** |
| **Average Input Tokens** | 38,400 | **14,060** | **63% token savings** |
| **Syntax Errors in Edits** | 4 occurrences | **0 occurrences** | **100% elimination** |

*Key finding*: Providing structured response slots and AST definition search dramatically cuts wandering steps, preventing the agent from exhausting its iteration and token budget.

---

## 6. Conclusions & Recommendations

### Selected Models for Production Pipeline
1. **Primary Model**: **Qwen 2.5 Coder 32B Instruct**
   - *Justification*: 100% solve rate across tested tasks, lowest token footprint, rapid response latency (1,150ms), and perfect submission discipline. Highly cost-effective on free/low-cost tiers.
2. **Alternative / High-Reasoning Tier**: **DeepSeek V3** & **Claude 3.5 Sonnet**
   - *Justification*: Superior single-shot exploration efficiency, locating root causes in 1–2 iterations.

### Disregarded Models
1. **Llama 3.3 70B Instruct**: Prone to over-exploration and hallucinating path prefixes in large repositories; highest token usage.
2. **GPT-4o mini**: While fast and economical, fails on subtle type algebra problems (e.g. `sympy-14711`) by producing superficial edits that fail regression tests.
