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

echo "============================================================"
echo "           AGENT SMITH: EXAM SANDBOX SECURITY TESTS         "
echo "============================================================"
echo "Student Path:    $STUDENT_PATH"
echo "Moulinette Path: $MOULINETTE_PATH"
echo "Pass Threshold:  ALL tests must pass"
echo "------------------------------------------------------------"

FAILED=0
TOTAL=0

run_test() {
  local name="$1"
  local code="$2"
  local expected="$3"
  TOTAL=$((TOTAL + 1))
  echo -n "Test $TOTAL [$name] ... "

  local out
  out=$(cd "$STUDENT_PATH" && uv run python -c "
import sandbox
from models import SandboxConfig

$code
" 2>&1 || true)

  if echo "$out" | grep -qi "$expected"; then
    echo -e "\033[32mPASSED\033[0m"
  else
    echo -e "\033[31mFAILED\033[0m"
    echo "  Expected output containing: $expected"
    echo "  Actual output: $out"
    FAILED=$((FAILED + 1))
  fi
}

# 1. Import block
run_test "import block" \
  "s = sandbox.ExecutionSandbox(); print(s.execute('import subprocess'))" \
  "blocked by sandbox security policy"

# 2. Builtin block
run_test "builtin block" \
  "s = sandbox.ExecutionSandbox(); print(s.execute('import os; os.system(\"echo hacked\")'))" \
  "blocked by sandbox security policy"

# 3. Network block
run_test "network block" \
  "cfg = SandboxConfig(authorized_imports=['socket']); s = sandbox.ExecutionSandbox(config=cfg); print(s.execute('import socket; sock = socket.socket()'))" \
  "network access is blocked"

# 4. Path restrict
run_test "path restrict" \
  "cfg = SandboxConfig(allowed_directories=['/testbed']); s = sandbox.ExecutionSandbox(config=cfg); print(s.execute('open(\"/etc/shadow\", \"r\")'))" \
  "denied by sandbox policy"

# 5. Timeout
run_test "timeout" \
  "cfg = SandboxConfig(max_execution_time_seconds=1); s = sandbox.ExecutionSandbox(config=cfg); print(s.execute('while True: pass'))" \
  "timeout"

# 6. Memory limit
run_test "memory limit" \
  "cfg = SandboxConfig(max_memory_mb=64); s = sandbox.ExecutionSandbox(config=cfg); print(s.execute('x = bytearray(128 * 1024 * 1024)'))" \
  "MemoryError"

# 7. MCP protocol
run_test "MCP protocol" \
  "from mcp_client import McpClientManager; import sys; m = McpClientManager(stdio_cmd=[sys.executable, 'mcp_tools_swebench.py']); print('TOOLS:', [t.name for t in m.tools]); m.close()" \
  "read_file"

echo "------------------------------------------------------------"
echo "Results: $((TOTAL - FAILED)) / $TOTAL tests passed."

if [[ $FAILED -eq 0 ]]; then
  echo -e "\033[32mEXAM SANDBOX: ALL TESTS PASSED!\033[0m"
  exit 0
else
  echo -e "\033[31mEXAM SANDBOX: FAILED ($FAILED errors)\033[0m"
  exit 1
fi
