"""Sandbox security enforcement using only standard Python libraries."""
import fnmatch
import os
import signal
import socket
import sys
import resource
from contextlib import contextmanager
from typing import List, Optional, Set


class FinalAnswerSignal(Exception):
    """Signal raised when code calls final_answer(answer_string)."""
    def __init__(self, answer: str):
        self.answer = str(answer)
        super().__init__(self.answer)


class SandboxTimeoutError(TimeoutError):
    """Raised when sandbox execution exceeds max execution time."""
    pass


class SandboxSecurityViolation(PermissionError):
    """Raised when sandboxed code attempts a forbidden operation."""
    pass


def is_import_allowed(module_name: str, authorized_imports: List[str]) -> bool:
    """Check if a module name matches any pattern in authorized_imports."""
    if not module_name:
        return False

    for pattern in authorized_imports:
        if pattern == module_name:
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            if module_name == prefix or module_name.startswith(prefix + "."):
                return True
        elif fnmatch.fnmatch(module_name, pattern):
            return True
        elif module_name.startswith(pattern + "."):
            return True

    return False


def is_path_allowed(filepath: str, allowed_directories: List[str]) -> bool:
    """Check if filepath resides within any directory in allowed_directories."""
    try:
        resolved_file = os.path.realpath(os.path.abspath(filepath))
    except Exception:
        return False

    for allowed_dir in allowed_directories:
        try:
            resolved_dir = os.path.realpath(os.path.abspath(allowed_dir))
            common = os.path.commonpath([resolved_file, resolved_dir])
            if common == resolved_dir:
                return True
        except Exception:
            continue

    return False


class SafeImportHook:
    """Import hook to enforce authorized_imports allowlist."""

    def __init__(self, authorized_imports: List[str]):
        self.authorized_imports = authorized_imports
        # Modules required internally by Python runtime to function
        self.internal_modules = {
            "sys", "builtins", "_thread", "_signal", "_io", "_codecs",
            "encodings", "encodings.utf_8", "encodings.aliases",
        }

    def check_import(self, name: str):
        if name in self.internal_modules:
            return
        if not is_import_allowed(name, self.authorized_imports):
            raise ImportError(
                f"Import of '{name}' is blocked by sandbox security policy. "
                f"Allowed imports: {self.authorized_imports}"
            )


class SandboxSecurityContext:
    """Context manager that activates sandbox constraints (timeout, network, open, imports)."""

    def __init__(
        self,
        authorized_imports: List[str],
        allowed_directories: List[str],
        max_execution_time_seconds: int = 30,
        max_memory_mb: int = 512,
    ):
        self.authorized_imports = authorized_imports
        self.allowed_directories = allowed_directories
        self.timeout = max_execution_time_seconds
        self.max_memory_mb = max_memory_mb

        self._orig_import = None
        self._orig_open = None
        self._orig_socket = None
        self._orig_create_connection = None
        self._orig_alarm_handler = None

    def _timeout_handler(self, signum, frame):
        raise SandboxTimeoutError(f"Execution timed out after {self.timeout} seconds")

    INTERNAL_MODULES = {
        "builtins", "sys", "_thread", "_signal", "_io", "_codecs",
        "encodings", "encodings.utf_8", "encodings.aliases",
        "asyncio", "asyncio.events", "asyncio.base_events", "asyncio.coroutines",
        "concurrent", "concurrent.futures", "concurrent.futures._base",
        "threading",
    }

    def _safe_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if name not in self.INTERNAL_MODULES and not is_import_allowed(name, self.authorized_imports):
            raise ImportError(
                f"Import of '{name}' is blocked by sandbox security policy. "
                f"Allowed imports: {self.authorized_imports}"
            )
        return self._orig_import(name, globals, locals, fromlist, level)

    def _safe_open(self, file, mode="r", *args, **kwargs):
        # Disallow opening if file is path-like or str and not in allowed_directories
        if isinstance(file, (str, os.PathLike)):
            file_str = str(file)
            if not is_path_allowed(file_str, self.allowed_directories):
                raise SandboxSecurityViolation(
                    f"Filesystem access to '{file_str}' is denied by sandbox policy. "
                    f"Allowed directories: {self.allowed_directories}"
                )
        return self._orig_open(file, mode, *args, **kwargs)

    def _blocked_socket(self, *args, **kwargs):
        raise SandboxSecurityViolation("Network access is blocked by sandbox security policy.")

    def __enter__(self):
        # 1. Override import
        import builtins
        self._orig_import = builtins.__import__
        builtins.__import__ = self._safe_import

        # 2. Override open
        self._orig_open = builtins.open
        builtins.open = self._safe_open

        # 3. Block network sockets
        self._orig_socket = socket.socket
        self._orig_create_connection = getattr(socket, "create_connection", None)
        socket.socket = self._blocked_socket
        if self._orig_create_connection:
            socket.create_connection = self._blocked_socket

        # 4. Enforce timeout via SIGALRM (on Unix)
        if hasattr(signal, "SIGALRM") and self.timeout > 0:
            self._orig_alarm_handler = signal.signal(signal.SIGALRM, self._timeout_handler)
            signal.alarm(self.timeout)

        # 5. Enforce memory limits if possible
        if hasattr(resource, "RLIMIT_AS") and self.max_memory_mb > 0:
            try:
                max_bytes = self.max_memory_mb * 1024 * 1024
                soft, hard = resource.getrlimit(resource.RLIMIT_AS)
                new_soft = max_bytes if soft == resource.RLIM_INFINITY else min(soft, max_bytes)
                resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
            except Exception:
                pass

        return self

    @contextmanager
    def pause(self):
        """Temporarily suspend sandbox restrictions for actions outside the sandbox (e.g. MCP tools)."""
        import builtins
        prev_import = builtins.__import__
        prev_open = builtins.open
        prev_socket = socket.socket
        prev_cc = getattr(socket, "create_connection", None)

        builtins.__import__ = self._orig_import
        builtins.open = self._orig_open
        socket.socket = self._orig_socket
        if self._orig_create_connection is not None:
            socket.create_connection = self._orig_create_connection
        try:
            yield
        finally:
            builtins.__import__ = prev_import
            builtins.open = prev_open
            socket.socket = prev_socket
            if prev_cc is not None:
                socket.create_connection = prev_cc

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Cancel alarm
        if hasattr(signal, "SIGALRM") and self.timeout > 0:
            signal.alarm(0)
            if self._orig_alarm_handler is not None:
                signal.signal(signal.SIGALRM, self._orig_alarm_handler)

        # Restore network
        if self._orig_socket is not None:
            socket.socket = self._orig_socket
        if self._orig_create_connection is not None:
            socket.create_connection = self._orig_create_connection

        # Restore open & import
        import builtins
        if self._orig_open is not None:
            builtins.open = self._orig_open
        if self._orig_import is not None:
            builtins.__import__ = self._orig_import

        return False  # Do not suppress exceptions
