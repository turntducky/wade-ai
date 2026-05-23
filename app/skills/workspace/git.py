import asyncio
import logging

from pathlib import Path
from typing import Optional, List

from app.skills.registry import register_tool
from app.core.utils import run_command_async

logger = logging.getLogger("wade.skills.git")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

async def _run_git(args: List[str]) -> str:
    cmd = ["git"] + args
    out, err, code = await run_command_async(cmd, timeout=30)
    
    if code == 0:
        return f"<git_stdout>\n{out if out else 'Command executed successfully.'}\n</git_stdout>"
    else:
        return f"<git_stderr>\n{err if err else 'Unknown git error.'}\n</git_stderr>\nReturn Code: {code}"

@register_tool("git_status")
async def git_status() -> str:
    return await _run_git(["status"])

@register_tool("git_diff")
async def git_diff(file_path: Optional[str] = None, staged: bool = False) -> str:
    args = ["diff"]
    if staged:
        args.append("--staged")
    if file_path:
        args.append(file_path)
    return await _run_git(args)

@register_tool("git_commit")
async def git_commit(message: str, all: bool = True) -> str:
    args = ["commit", "-m", message]
    if all:
        args.append("-a")
    return await _run_git(args)

@register_tool("git_branch")
async def git_branch(action: str, name: Optional[str] = None) -> str:
    if action == "list":
        return await _run_git(["branch"])
    elif action == "create":
        if not name: return "Error: Branch name required for creation."
        return await _run_git(["branch", name])
    elif action == "delete":
        if not name: return "Error: Branch name required for deletion."
        return await _run_git(["branch", "-d", name])
    return "Error: Invalid branch action."

@register_tool("git_checkout")
async def git_checkout(target: str, create_branch: bool = False) -> str:
    args = ["checkout"]
    if create_branch:
        args.append("-b")
    args.append(target)
    return await _run_git(args)

@register_tool("git_restore")
async def git_restore(file_path: str) -> str:
    return await _run_git(["restore", file_path])