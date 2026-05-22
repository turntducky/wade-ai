from __future__ import annotations

import os
import sys
import json
import struct
import shutil
import ctypes
import asyncio
import logging
import platform
import importlib
import ctypes.util
import concurrent.futures

from pathlib import Path

logger = logging.getLogger("wade.sandbox")

_BWRAP: str | None = shutil.which("bwrap")

_BWRAP_OK: bool = True

_RUNNER_SRC = """\
import sys, json, asyncio, importlib

try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS,     (4 * 1024 ** 3, 4 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_CPU,    (60, 60))
    resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
except Exception:
    pass

data = json.load(sys.stdin)
mod  = importlib.import_module(data["module"])
func = getattr(mod, data["func"])
if asyncio.iscoroutinefunction(func):
    result = asyncio.run(func(**data["kwargs"]))
else:
    result = func(**data["kwargs"])
sys.stdout.write(json.dumps(str(result)))
sys.stdout.flush()
"""

_RUNNER_PATH: Path | None = None

def _ensure_runner() -> Path:
    """Write the bwrap runner script to ~/.wade/sandbox/ (idempotent)."""
    global _RUNNER_PATH
    if _RUNNER_PATH is not None and _RUNNER_PATH.exists():
        return _RUNNER_PATH
    sandbox_dir = Path.home() / ".wade" / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    runner = sandbox_dir / "_bwrap_runner.py"
    runner.write_text(_RUNNER_SRC, encoding="utf-8")
    _RUNNER_PATH = runner
    return runner

class _BwrapUnavailableError(RuntimeError):
    """Raised when the bwrap binary itself fails to start (not a tool error)."""

async def _run_in_bwrap(module_name: str, func_name: str, kwargs: dict, requires_network: bool) -> str:
    """Execute a tool inside a bubblewrap container."""
    try:
        payload = json.dumps(
            {"module": module_name, "func": func_name, "kwargs": kwargs}
        )
    except (TypeError, ValueError) as exc:
        return f"Sandbox error: non-serialisable arguments — {exc}"

    runner      = _ensure_runner()
    sandbox_dir = runner.parent

    cmd = [
        _BWRAP,                                             # type: ignore[list-item]
        "--ro-bind",   "/",   "/",                          # read-only host fs
        "--bind",      str(sandbox_dir), str(sandbox_dir),  # writable sandbox
        "--dev",       "/dev",                              # device access
        "--proc",      "/proc",                             # fresh /proc
        "--unshare-pid",                                    # PID namespace
        "--unshare-ipc",                                    # IPC namespace
        "--unshare-uts",                                    # hostname namespace
        "--die-with-parent",                                # lifecycle binding
    ]
    if not requires_network:
        cmd.append("--unshare-net")

    cmd += [sys.executable, str(runner)]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise _BwrapUnavailableError(str(exc)) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload.encode()),
            timeout=65.0,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Sandbox error: tool timed out inside bwrap container"

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:500]
        if not err:
            raise _BwrapUnavailableError(
                f"bwrap exited {proc.returncode} with no stderr "
                "(user namespaces may be disabled on this kernel)"
            )
        return f"Sandbox error (exit {proc.returncode}): {err}"

    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        return stdout.decode(errors="replace")

_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP      = 22
_SECCOMP_MODE_FILTER = 2

_BPF_LD  = 0x00
_BPF_W   = 0x00
_BPF_ABS = 0x20
_BPF_JMP = 0x05
_BPF_JEQ = 0x10
_BPF_K   = 0x00
_BPF_RET = 0x06

_SECCOMP_RET_ALLOW        = 0x7FFF_0000
_SECCOMP_RET_KILL_PROCESS = 0x8000_0000

_OFF_NR   = 0   # seccomp_data.nr offset
_OFF_ARCH = 4   # seccomp_data.arch offset

_AUDIT_ARCH_X86_64  = 0xC000_003E
_AUDIT_ARCH_AARCH64 = 0xC000_00B7

_BLOCKED_X86_64: list[int] = [
    101,  # ptrace            process injection / tracing
    105,  # setuid            UID change
    106,  # setgid            GID change
    116,  # setgroups         supplementary group manipulation
    165,  # mount             filesystem mounting
    166,  # umount2           filesystem unmounting
    169,  # reboot            system reboot / kexec trigger
    175,  # init_module       kernel module loading (legacy)
    176,  # delete_module     kernel module unloading
    246,  # kexec_load        live kernel replacement
    298,  # perf_event_open   hardware perf counters (side-channel vector)
    313,  # finit_module      kernel module loading (fd-based)
    317,  # seccomp           prevent self-modification of the filter
    321,  # bpf               eBPF program loading (kernel code injection)
    323,  # userfaultfd       userspace fault handling (exploitation primitive)
    424,  # pidfd_send_signal cross-process signalling via pidfd
    425,  # io_uring_setup    io_uring can bypass seccomp in some kernel versions
    426,  # io_uring_enter
    427,  # io_uring_register
]

_BLOCKED_AARCH64: list[int] = [
    39,   # umount2
    40,   # mount
    104,  # kexec_load
    105,  # init_module
    106,  # delete_module
    117,  # ptrace
    142,  # reboot
    144,  # setgid
    146,  # setuid
    159,  # setgroups
    241,  # perf_event_open
    273,  # finit_module
    277,  # seccomp
    280,  # bpf
    282,  # userfaultfd
    424,  # pidfd_send_signal
    425,  # io_uring_setup
    426,  # io_uring_enter
    427,  # io_uring_register
]

class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt",   ctypes.c_uint8),
        ("jf",   ctypes.c_uint8),
        ("k",    ctypes.c_uint32),
    ]

class _SockFProg(ctypes.Structure):
    _fields_ = [
        ("len",    ctypes.c_uint16),
        ("filter", ctypes.POINTER(_SockFilter)),
    ]

def _bpf_instr(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("<HBBI", code, jt, jf, k)

def _build_blocklist_filter(arch_const: int, blocked: list[int]) -> bytes:
    """
    Build a BPF program:
      [0]      LOAD  arch field
      [1]      JEQ   arch_const  jt=0(ok) jf=n+2(→KILL)   — fail-closed on wrong arch
      [2]      LOAD  syscall-nr field
      [3..n+2] JEQ   blocked[i]  jt=(n-i)(→KILL) jf=0
      [n+3]    RET   ALLOW
      [n+4]    RET   KILL_PROCESS
    """
    n = len(blocked)
    out: list[bytes] = []

    out.append(_bpf_instr(_BPF_LD | _BPF_W | _BPF_ABS, 0, 0, _OFF_ARCH))
    out.append(_bpf_instr(_BPF_JMP | _BPF_JEQ | _BPF_K, 0, n+2, arch_const))
    out.append(_bpf_instr(_BPF_LD | _BPF_W | _BPF_ABS, 0, 0, _OFF_NR))
    for i, nr in enumerate(blocked):
        out.append(_bpf_instr(_BPF_JMP | _BPF_JEQ | _BPF_K, n-i, 0, nr))
    out.append(_bpf_instr(_BPF_RET | _BPF_K, 0, 0, _SECCOMP_RET_ALLOW))
    out.append(_bpf_instr(_BPF_RET | _BPF_K, 0, 0, _SECCOMP_RET_KILL_PROCESS))

    return b"".join(out)

def _apply_seccomp(arch_const: int, blocked: list[int]) -> None:
    lib = ctypes.util.find_library("c")
    if not lib:
        raise RuntimeError("libc not found")
    libc = ctypes.CDLL(lib, use_errno=True)
    libc.prctl.restype  = ctypes.c_int
    libc.prctl.argtypes = [
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_ulong, ctypes.c_ulong,
    ]

    raw = _build_blocklist_filter(arch_const, blocked)
    n   = len(raw) // 8
    arr = (_SockFilter * n).from_buffer_copy(raw)
    fp  = _SockFProg(len=n, filter=arr)

    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"prctl(PR_SET_NO_NEW_PRIVS) errno={errno}")
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(fp), 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"prctl(PR_SET_SECCOMP) errno={errno}")

    logger.debug("[SANDBOX] seccomp blocklist applied (%d syscalls blocked)", len(blocked))


def _maybe_apply_seccomp() -> None:
    if sys.platform != "linux":
        return
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch_const, blocked = _AUDIT_ARCH_X86_64,  _BLOCKED_X86_64
    elif machine in ("aarch64", "arm64"):
        arch_const, blocked = _AUDIT_ARCH_AARCH64, _BLOCKED_AARCH64
    else:
        logger.debug("[SANDBOX] seccomp: arch '%s' not in table — skipped", machine)
        return
    try:
        _apply_seccomp(arch_const, blocked)
    except OSError as exc:
        logger.warning("[SANDBOX] seccomp not applied: %s", exc)
    except Exception as exc:
        logger.warning("[SANDBOX] seccomp error: %s", exc)

_RLIMIT_AS_BYTES    = 4 * 1024 ** 3   # 4 GB virtual-memory cap
_RLIMIT_CPU_SECONDS = 60
_RLIMIT_NOFILE      = 512

def _apply_rlimits() -> None:
    from typing import Any
    import resource
    _res: Any = resource

    try:
        _res.setrlimit(_res.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))
        logger.debug("[SANDBOX] RLIMIT_AS  = 4 GB")
    except Exception as exc:
        logger.debug("[SANDBOX] RLIMIT_AS skipped: %s", exc)

    try:
        _res.setrlimit(_res.RLIMIT_CPU, (_RLIMIT_CPU_SECONDS, _RLIMIT_CPU_SECONDS))
        logger.debug("[SANDBOX] RLIMIT_CPU = %ds", _RLIMIT_CPU_SECONDS)
    except Exception as exc:
        logger.debug("[SANDBOX] RLIMIT_CPU skipped: %s", exc)

    try:
        soft, hard = _res.getrlimit(_res.RLIMIT_NOFILE)
        cap = min(_RLIMIT_NOFILE, hard) if hard != _res.RLIM_INFINITY else _RLIMIT_NOFILE
        _res.setrlimit(_res.RLIMIT_NOFILE, (cap, hard))
        logger.debug("[SANDBOX] RLIMIT_NOFILE = %d", cap)
    except Exception as exc:
        logger.debug("[SANDBOX] RLIMIT_NOFILE skipped: %s", exc)


_WIN_JOB_HANDLE: int | None = None

_JOB_OBJECT_LIMIT_PROCESS_MEMORY       = 0x0000_0100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE    = 0x0000_2000
_JobObjectExtendedLimitInformation      = 9

def _apply_windows_job_limits() -> None:
    """Create a Windows Job Object that caps each worker's virtual memory to 4 GB."""
    global _WIN_JOB_HANDLE
    if _WIN_JOB_HANDLE is not None:
        return

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

        class _BasicLimitInfo(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit",     ctypes.c_int64),
                ("LimitFlags",             ctypes.c_uint32),
                ("MinimumWorkingSetSize",  ctypes.c_size_t),
                ("MaximumWorkingSetSize",  ctypes.c_size_t),
                ("ActiveProcessLimit",     ctypes.c_uint32),
                ("Affinity",              ctypes.c_size_t),
                ("PriorityClass",         ctypes.c_uint32),
                ("SchedulingClass",       ctypes.c_uint32),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [(f, ctypes.c_uint64) for f in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount",  "WriteTransferCount",  "OtherTransferCount",
            )]

        class _ExtendedLimitInfo(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation",  _BasicLimitInfo),
                ("IoInfo",                 _IoCounters),
                ("ProcessMemoryLimit",     ctypes.c_size_t),
                ("JobMemoryLimit",         ctypes.c_size_t),
                ("PeakProcessMemoryUsed",  ctypes.c_size_t),
                ("PeakJobMemoryUsed",      ctypes.c_size_t),
            ]

        hJob = kernel32.CreateJobObjectW(None, None)
        if not hJob:
            logger.warning("[SANDBOX] CreateJobObjectW failed: %d", ctypes.get_last_error())
            return

        info = _ExtendedLimitInfo()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_PROCESS_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info.ProcessMemoryLimit = _RLIMIT_AS_BYTES

        if not kernel32.SetInformationJobObject(
            hJob, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            logger.warning("[SANDBOX] SetInformationJobObject failed: %d", ctypes.get_last_error())
            kernel32.CloseHandle(hJob)
            return

        if not kernel32.AssignProcessToJobObject(hJob, kernel32.GetCurrentProcess()):
            err = ctypes.get_last_error()
            logger.debug(
                "[SANDBOX] AssignProcessToJobObject failed: %d "
                "(process may already be in a non-nested job — limits not applied)", err
            )
            kernel32.CloseHandle(hJob)
            return

        _WIN_JOB_HANDLE = hJob
        logger.debug("[SANDBOX] Windows Job Object: 4 GB process memory limit applied")

    except Exception as exc:
        logger.warning("[SANDBOX] Windows Job Object setup failed: %s", exc)

_PROCESS_POOL: concurrent.futures.ProcessPoolExecutor | None = None

def _sandbox_init() -> None:
    """
    Initializer for each ProcessPoolExecutor worker (fallback path only).

    On Linux with bubblewrap the process pool is bypassed entirely; this
    initializer runs only when bwrap is unavailable or not applicable.

    Isolation applied
    -----------------
    - chdir → ~/.wade/sandbox
    - umask(0o077)                         UNIX
    - RLIMIT_AS / CPU / NOFILE             UNIX
    - PR_SET_NO_NEW_PRIVS + seccomp BPF    Linux x86-64 / arm64
    - Windows Job Object (4 GB mem cap)    Windows
    """
    sandbox_dir = Path.home() / ".wade" / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(sandbox_dir)

    if sys.platform == "win32":
        _apply_windows_job_limits()
        return

    try:
        os.umask(0o077)
    except Exception:
        pass

    _apply_rlimits()
    _maybe_apply_seccomp()

def get_process_pool() -> concurrent.futures.ProcessPoolExecutor:
    global _PROCESS_POOL
    if _PROCESS_POOL is None:
        _PROCESS_POOL = concurrent.futures.ProcessPoolExecutor(
            max_workers=4,
            initializer=_sandbox_init,
        )
    return _PROCESS_POOL

def _run_tool_sync(module_name: str, func_name: str, kwargs: dict) -> str:
    """Run a tool function synchronously inside a worker process."""
    try:
        if module_name in sys.modules:
            del sys.modules[module_name]
        mod  = importlib.import_module(module_name)
        importlib.reload(mod)
        func = getattr(mod, func_name)
        if asyncio.iscoroutinefunction(func):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return str(loop.run_until_complete(func(**kwargs)))
            finally:
                loop.close()
        return str(func(**kwargs))
    except Exception as exc:
        return f"Sandbox execution error: {exc}"

async def run_in_sandbox(func, kwargs: dict, requires_network: bool = True) -> str:
    """Execute a registered tool function in the most isolated environment available."""
    global _BWRAP_OK

    module_name = getattr(func, "__module__", None)
    func_name   = getattr(func, "__name__",   None)

    if not module_name or not func_name:
        if asyncio.iscoroutinefunction(func):
            return str(await func(**kwargs))
        return str(func(**kwargs))

    if _BWRAP and _BWRAP_OK and sys.platform == "linux":
        try:
            return await _run_in_bwrap(module_name, func_name, kwargs, requires_network)
        except _BwrapUnavailableError as exc:
            _BWRAP_OK = False
            logger.warning(
                "[SANDBOX] bubblewrap unavailable — falling back to process pool "
                "for all future calls. Reason: %s", exc
            )

    pool = get_process_pool()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            pool, _run_tool_sync, module_name, func_name, kwargs,
        )
    except Exception as exc:
        logger.error("[SANDBOX] Execution failed: %s", exc)
        return f"Sandbox execution error: {exc}"