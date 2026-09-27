#!/usr/bin/env bash
set -euo pipefail

# Default arguments
STUDENT_PATH="."
MOULINETTE_PATH="./moulinette"
ENV_FILE=""

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --student-path)
      STUDENT_PATH="$2"
      shift 2
      ;;
    --moulinette-path)
      MOULINETTE_PATH="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -n "$ENV_FILE" && -f "$ENV_FILE" ]]; then
  export $(grep -v '^#' "$ENV_FILE" | xargs -d '\n' 2>/dev/null || true)
fi

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
EVAL_DIR="./evaluations/swebench/$TIMESTAMP"
mkdir -p "$EVAL_DIR"

echo "============================================================"
echo "            AGENT SMITH: EXAM SWE-BENCH EVALUATION          "
echo "============================================================"
echo "Student Path:    $STUDENT_PATH"
echo "Moulinette Path: $MOULINETTE_PATH"
echo "Log Directory:   $EVAL_DIR"
echo "Pass Threshold:  2 / 3 tasks"
echo "Limits:          30 iterations, 300k in-tokens, 10k out-tokens, 900s"
echo "------------------------------------------------------------"

PASSED_COUNT=0
TOTAL_TASKS=3

# Tasks from exam pool
TASKS=("sympy__sympy-23534" "sympy__sympy-14711" "pydata__xarray-4629")

for (( i=0; i<$TOTAL_TASKS; i++ )); do
  TASK_ID="${TASKS[$i]}"
  TASK_DIR="$EVAL_DIR/$TASK_ID"
  mkdir -p "$TASK_DIR"

  TASK_FILE="$TASK_DIR/task.json"
  SOLUTION_FILE="$TASK_DIR/solution.json"
  STDOUT_LOG="$TASK_DIR/stdout.log"
  STDERR_LOG="$TASK_DIR/stderr.log"

  echo -n "Task $((i + 1))/$TOTAL_TASKS ($TASK_ID) ... "

  # 1. Dump task
  (cd "$MOULINETTE_PATH" && uv run moulinette_eval dump swebench --task-id "$TASK_ID" --output "../$TASK_FILE") >/dev/null 2>&1 || true

  if [[ ! -f "$TASK_FILE" ]]; then
    # Fallback to local dump if network/dataset unavailable
    echo "{\"instance_id\": \"$TASK_ID\", \"problem_statement\": \"Fix issue in $TASK_ID\", \"docker_image\": \"\", \"eval_script\": \"\", \"repo\": \"\"}" > "$TASK_FILE"
  fi

  # 2. Run student agent
  MODEL_NAME="${MODEL_NAME:-qwen/qwen-2.5-coder-32b-instruct}"
  PROVIDER_URL="${PROVIDER_URL:-https://openrouter.ai/api/v1}"
  if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GROQ_API_KEY:-}" ]]; then
    export AGENT_MOCK_LLM=1
    MODEL_NAME="mock"
    PROVIDER_URL="mock"
  fi

  (cd "$STUDENT_PATH" && uv run python -m agent_swebench \
    --task-file "../$TASK_FILE" \
    --output "../$SOLUTION_FILE" \
    --model-name "$MODEL_NAME" \
    --provider-url "$PROVIDER_URL" \
    --max-iterations 30 \
    --max-input-tokens 300000 \
    --max-output-tokens 10000 \
    --timeout 900) > "$STDOUT_LOG" 2> "$STDERR_LOG" || true

  # 3. Validate metrics and correctness
  VALID_STATUS=0
  if [[ -f "$SOLUTION_FILE" ]]; then
    if (cd "$MOULINETTE_PATH" && uv run moulinette_eval validate_metrics swebench "../$SOLUTION_FILE") >> "$STDOUT_LOG" 2>> "$STDERR_LOG"; then
      VALID_STATUS=1
    fi
  fi

  # 4. Container cleanup
  if command -v docker >/dev/null 2>&1; then
    docker ps -q --filter "name=$TASK_ID" | xargs -r docker rm -f >/dev/null 2>&1 || true
  fi

  if [[ $VALID_STATUS -eq 1 ]]; then
    echo -e "\033[32mPASSED\033[0m"
    PASSED_COUNT=$((PASSED_COUNT + 1))
  else
    echo -e "\033[31mFAILED\033[0m"
  fi
done

echo "------------------------------------------------------------"
echo "Results: $PASSED_COUNT / $TOTAL_TASKS passed."

if [[ $PASSED_COUNT -ge 2 ]]; then
  echo -e "\033[32mEXAM SWE-BENCH: PASSED (Threshold 2/3 met)!\033[0m"
  exit 0
else
  echo -e "\033[31mEXAM SWE-BENCH: FAILED (Threshold 2/3 not met)\033[0m"
  exit 1
fi
