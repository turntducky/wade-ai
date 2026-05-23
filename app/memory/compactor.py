import re

from pathlib import Path

MEMORY_DIR = Path.home() / ".wade" / "workspace" / "memory"
MAX_SESSION_FILES = 10
MAX_SESSION_CHARS = 50000

def prune_old_sessions():
    """Scans the memory directory and deletes session files exceeding the max limit."""
    if not MEMORY_DIR.exists():
        return
        
    session_files = [
        f for f in MEMORY_DIR.glob("*.md")
        if re.match(r"^\d{2}-\d{2}-\d{2}(_[a-zA-Z0-9]+)?\.md$", f.name)
    ]
    
    session_files.sort(key=lambda f: f.stat().st_mtime)
    
    if len(session_files) > MAX_SESSION_FILES:
        files_to_delete = session_files[:-MAX_SESSION_FILES]
        for file_path in files_to_delete:
            try:
                file_path.unlink()
                print(f"🧹 Memory Compactor: Deleted old session '{file_path.name}'")
            except Exception as e:
                print(f"⚠️ Memory Compactor: Failed to delete '{file_path.name}': {e}")

def _build_drop_summary(dropped_blocks: list[str], max_chars: int = 1800) -> str:
    """Builds a summary string for the dropped conversation blocks when truncating a session. It extracts the role (e.g. user, assistant) and a snippet of the content from each dropped block, and compiles them into a concise summary that indicates how many exchanges were condensed. The summary is truncated to max_chars if it exceeds that length."""
    lines: list[str] = []
    for block in dropped_blocks:
        raw_lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not raw_lines:
            continue
        role = raw_lines[0].lstrip("#").strip() if raw_lines[0].startswith("#") else "?"
        content_lines = [l for l in raw_lines[1:] if l and not l.startswith("#")]
        if not content_lines:
            continue
        snippet = content_lines[0][:120]
        lines.append(f"  {role}: {snippet}")

    if not lines:
        return ""

    header = f"[EARLIER CONVERSATION — {len(dropped_blocks)} exchanges condensed]\n"
    body = "\n".join(lines)
    full = header + body + "\n\n"

    if len(full) > max_chars:
        full = full[:max_chars].rsplit("\n", 1)[0] + "\n  ...(more)\n\n"
    return full

def truncate_active_session(session_file: Path):
    """Truncates the active session file if it exceeds the maximum character limit. It retains the most recent part of the conversation while summarizing the dropped earlier exchanges. The function reads the session file, checks its length, and if it exceeds MAX_SESSION_CHARS, it splits the content into blocks (using "\n\n---\n\n" as a delimiter), retains the most recent blocks that fit within 60% of MAX_SESSION_CHARS, and builds a summary for the dropped blocks. The truncated content is then written back to the session file."""
    if not session_file.exists():
        return

    if session_file.stat().st_size < MAX_SESSION_CHARS:
        return

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            content = f.read()

        if len(content) > MAX_SESSION_CHARS:
            blocks = content.split("\n\n---\n\n")
            keep_length = int(MAX_SESSION_CHARS * 0.6)

            retained_blocks: list[str] = []
            current_length = 0

            for block in reversed(blocks):
                if not block.strip():
                    continue
                block_len = len(block) + 9
                if current_length + block_len > keep_length:
                    break
                retained_blocks.insert(0, block)
                current_length += block_len

            retained_set = set(id(b) for b in retained_blocks)
            all_non_empty = [b for b in blocks if b.strip()]
            dropped_blocks = [b for b in all_non_empty if id(b) not in retained_set]
            summary = _build_drop_summary(dropped_blocks)
            truncated_content = summary + "\n\n---\n\n".join(retained_blocks) + "\n\n---\n\n"

            with open(session_file, "w", encoding="utf-8") as f:
                f.write(truncated_content)

            print(f"✂️ Memory Compactor: Condensed session '{session_file.name}' "
                  f"({len(dropped_blocks)} exchanges summarised, {len(retained_blocks)} retained)")
    except Exception as e:
        print(f"⚠️ Memory Compactor: Failed to truncate '{session_file.name}': {e}")