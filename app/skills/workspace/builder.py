import os
import asyncio
import logging

from pathlib import Path
from typing import Dict, Any

from app.skills.registry import register_tool
from app.core.utils import run_command_async

logger = logging.getLogger("wade.skills.builder")

@register_tool("run_polyglot")
async def run_polyglot(file_path: str, args: str = "") -> str:
    """Detects language and attempts to build/run the file."""
    path = Path(file_path)
    if not path.exists():
        return f"Error: File {file_path} not found."
    
    ext = path.suffix.lower()
    filename = path.stem
    cwd = str(path.parent)
    
    commands = {
        ".cpp": f"g++ {path.name} -o {filename} && ./{filename} {args}" if os.name != 'nt' else f"g++ {path.name} -o {filename}.exe && {filename}.exe {args}",
        ".c":   f"gcc {path.name} -o {filename} && ./{filename} {args}" if os.name != 'nt' else f"gcc {path.name} -o {filename}.exe && {filename}.exe {args}",
        ".java": f"javac {path.name} && java {filename} {args}",
        ".cs":   f"csc {path.name} && {filename}.exe {args}" if os.name == 'nt' else f"dotnet run {path.name} -- {args}",
        ".py":   f"python {path.name} {args}",
        ".js":   f"node {path.name} {args}",
        ".swift": f"swift {path.name} {args}",
        ".mql4": f"metaeditor.exe /compile:{path.name}",
        ".mql5": f"metaeditor.exe /compile:{path.name}",
    }

    if ext not in commands:
        return f"Error: Language extension '{ext}' is not currently mapped for execution. Use run_shell_command manually."

    cmd = commands[ext]
    logger.info(f"[BUILDER] Executing: {cmd} in {cwd}")
    
    out, err, code = await run_command_async(cmd, shell=True, timeout=30)
    
    status = "SUCCESS" if code == 0 else "FAILED"
    report = [f"--- Execution Report [{status}] ---"]
    if out: report.append(f"<stdout>\n{out}\n</stdout>")
    if err: report.append(f"<stderr>\n{err}\n</stderr>")
    
    return "\n".join(report)