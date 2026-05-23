import asyncio
import subprocess

from pathlib import Path

from app.skills.registry import register_tool

async def _run_git_command(cmd: list[str], cwd: str) -> str:
    def _run():
        try:
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"Git error: {e.stderr.strip()}"
        except FileNotFoundError:
            return "Git is not installed or not in PATH."
    return await asyncio.to_thread(_run)

@register_tool("code_review")
async def code_review(target_dir: str = ".") -> str:
    """Performs a code review by analyzing the git status of the target directory. It checks for unstaged and staged changes, as well as untracked files, and compiles a report for W.A.D.E to critique against architectural constraints."""
    target_path = Path(target_dir).resolve()
    if not target_path.exists():
        return f"ERROR: Target directory {target_path} does not exist."

    diff_unstaged = await _run_git_command(["git", "diff"], cwd=str(target_path))
    diff_staged = await _run_git_command(["git", "diff", "--cached"], cwd=str(target_path))
    
    untracked_files_raw = await _run_git_command(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=str(target_path)
    )
    
    untracked_content = []
    if untracked_files_raw and not untracked_files_raw.startswith("Git error"):
        for file_name in untracked_files_raw.splitlines():
            file_path = target_path / file_name
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(content.splitlines()))
                    untracked_content.append(f"--- UNTRACKED FILE: {file_name} ---\n{numbered}\n")
                except Exception as e:
                    untracked_content.append(f"--- UNTRACKED FILE: {file_name} (Could not read: {e}) ---\n")

    report = "### CODE REVIEW PACKET ###\n\n"
    
    if diff_staged:
        report += "#### STAGED CHANGES ####\n```diff\n" + diff_staged + "\n```\n\n"
    if diff_unstaged:
        report += "#### UNSTAGED CHANGES ####\n```diff\n" + diff_unstaged + "\n```\n\n"
    if untracked_content:
        report += "#### NEW/UNTRACKED FILES ####\n" + "\n".join(untracked_content) + "\n\n"
        
    if not diff_staged and not diff_unstaged and not untracked_content:
        return "STATUS: clean\n\nNo uncommitted changes found to review."

    report += (
        "INSTRUCTIONS FOR W.A.D.E:\n"
        "1. Analyze the changes above against the core architecture constraints.\n"
        "2. Output your critique using the strict format defined in your persona.\n"
        "3. If critical issues are found, use `workspace/patch_host_file` or `dev_file` to propose fixes."
    )
    
    return report