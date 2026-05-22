from pathlib import Path

from app.skills.registry import register_tool
from app.skills.dev.dev_file import (
    _resolve_language,
    _run_file,
    _pip_install,
    _extract_package,
    INTERPRETERS,
    EXEC_TIMEOUT,
)

def _read_all(paths: list[str]) -> list[tuple[str, str]]:
    result = []
    for p in paths:
        try:
            content = Path(p).read_text(encoding="utf-8")
        except Exception:
            content = "(unreadable after execution)"
        result.append((p, content))
    return result

def _format_multi_report(
    status: str,
    output: str,
    files: list[tuple[str, str]],
) -> str:
    header = (
        f"STATUS: {status}\n\n"
        f"EXECUTION OUTPUT:\n{output or 'No output.'}\n\n"
        f"FILES WRITTEN ({len(files)} total):\n"
    )
    blocks = []
    for i, (path, content) in enumerate(files, 1):
        numbered = "\n".join(
            f"{j + 1:4d} | {line}" for j, line in enumerate(content.splitlines())
        ) or "(empty file)"
        blocks.append(f"\n--- FILE {i}: {path} ---\n{numbered}")
    return header + "\n".join(blocks)

@register_tool("dev_files")
async def dev_files(files: list, entrypoint: str | None = None) -> str:
    """Write multiple interdependent files to disk, then execute the entrypoint."""
    if not files:
        return "ERROR: 'files' list is empty."

    for i, f in enumerate(files):
        if not isinstance(f, dict) or not f.get("file_path") or not f.get("code"):
            return f"ERROR: files[{i}] must have 'file_path' and 'code' keys."

    if len(files) > 1 and not entrypoint:
        return (
            "ERROR: 'entrypoint' is required when writing multiple files. "
            "Specify the file_path of the file to execute."
        )
    entry_path = entrypoint or files[0]["file_path"]
    entry_file = next((f for f in files if f["file_path"] == entry_path), None)
    if entry_file is None:
        return f"ERROR: entrypoint '{entry_path}' is not listed in files."

    lang = _resolve_language(entry_path, entry_file.get("language"))
    if not lang or lang not in INTERPRETERS:
        return (
            f"ERROR: Cannot determine language for '{Path(entry_path).name}'. "
            "Specify 'language' on the entrypoint file entry."
        )

    ordered_paths: list[str] = []
    for f in files:
        p = Path(f["file_path"])
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["code"], encoding="utf-8")
            ordered_paths.append(f["file_path"])
        except Exception as e:
            return f"ERROR: Could not write '{f['file_path']}': {e}"

    cmd = INTERPRETERS[lang]
    output, exit_code = await _run_file(cmd, entry_path, EXEC_TIMEOUT)

    if exit_code == 0:
        return _format_multi_report("success", output, _read_all(ordered_paths))

    status = "runtime_error"
    if lang == "python" and "ModuleNotFoundError" in output:
        package = _extract_package(output)
        if package:
            pip_ok, pip_out = await _pip_install(package)
            if not pip_ok:
                combined = f"pip install {package} failed:\n{pip_out}\n\nOriginal error:\n{output}"
                return _format_multi_report("install_failed", combined, _read_all(ordered_paths))
            output, exit_code = await _run_file(cmd, entry_path, EXEC_TIMEOUT)
            status = "fixed_after_install" if exit_code == 0 else "runtime_error"

    return _format_multi_report(status, output, _read_all(ordered_paths))