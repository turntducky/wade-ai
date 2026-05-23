import re
import sys
import asyncio

from pathlib import Path

from app.skills.registry import register_tool

INTERPRETERS = {
    "python": [sys.executable, "{path}"],
    "node":   ["node", "{path}"],
    "bash":   ["bash", "{path}"],
}

EXTENSION_MAP = {
    ".py": "python",
    ".js": "node",
    ".ts": "node",
    ".sh": "bash",
}

EXEC_TIMEOUT = 15
PIP_TIMEOUT  = 30

def _resolve_language(file_path: str, language: str | None) -> str | None:
    if language:
        return language.lower()
    return EXTENSION_MAP.get(Path(file_path).suffix.lower())

def _extract_package(error_output: str) -> str | None:
    match = re.search(r"No module named '([^']+)'", error_output)
    if not match:
        return None
    return match.group(1).split(".")[0]

def _format_report(status: str, output: str, file_path: str, content: str) -> str:
    numbered = "\n".join(
        f"{i + 1:4d} | {line}" for i, line in enumerate(content.splitlines())
    ) or "(empty file)"
    return (
        f"STATUS: {status}\n\n"
        f"EXECUTION OUTPUT:\n{output or 'No output.'}\n\n"
        f"ACTUAL FILE CONTENT ({file_path}):\n{numbered}"
    )

async def _run_file(cmd_template: list[str], file_path: str, timeout: int) -> tuple[str, int]:
    cmd = [c if c != "{path}" else file_path for c in cmd_template]

    def _run() -> tuple[str, int]:
        import subprocess
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return (result.stdout + result.stderr).strip(), result.returncode
        except subprocess.TimeoutExpired:
            return f"ERROR: Execution timed out after {timeout} seconds.", 1

    return await asyncio.to_thread(_run)

async def _pip_install(package: str) -> tuple[bool, str]:
    def _install() -> tuple[bool, str]:
        import subprocess
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True, text=True, timeout=PIP_TIMEOUT,
            )
            return result.returncode == 0, (result.stdout + result.stderr).strip()
        except subprocess.TimeoutExpired:
            return False, f"pip install timed out after {PIP_TIMEOUT} seconds."

    return await asyncio.to_thread(_install)

@register_tool("dev_file")
async def dev_file(file_path: str, code: str, language: str | None = None) -> str:
    lang = _resolve_language(file_path, language)
    if not lang or lang not in INTERPRETERS:
        return (
            f"ERROR: Cannot determine language for '{Path(file_path).name}'. "
            "Specify the 'language' parameter explicitly."
        )

    p = Path(file_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
    except Exception as e:
        return f"ERROR: Could not write file: {e}"

    cmd = INTERPRETERS[lang]
    output, exit_code = await _run_file(cmd, file_path, EXEC_TIMEOUT)

    if exit_code == 0:
        content = p.read_text(encoding="utf-8")
        return _format_report("success", output, file_path, content)

    if lang == "python" and "ModuleNotFoundError" in output:
        package = _extract_package(output)
        if package:
            pip_ok, pip_out = await _pip_install(package)
            if pip_ok:
                output, exit_code = await _run_file(cmd, file_path, EXEC_TIMEOUT)
                content = p.read_text(encoding="utf-8")
                status = "fixed_after_install" if exit_code == 0 else "runtime_error"
                return _format_report(status, output, file_path, content)
            else:
                content = p.read_text(encoding="utf-8")
                combined = (
                    f"pip install {package} failed:\n{pip_out}\n\n"
                    f"Original error:\n{output}"
                )
                return _format_report("install_failed", combined, file_path, content)

    content = p.read_text(encoding="utf-8")
    return _format_report("runtime_error", output, file_path, content)