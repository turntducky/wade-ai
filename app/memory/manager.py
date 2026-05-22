import json
import time

from pathlib import Path
from datetime import datetime

from app.memory.compactor import prune_old_sessions, truncate_active_session
from app.memory.episodes import get_episode_store as _get_episode_store, Episode as _Episode

MEMORY_DIR = Path.home() / ".wade" / "workspace" / "memory"
_last_prune: float = 0.0

def get_current_memory_file(session_id: str | None = None, memory_dir: Path | None = None, conv_id: str | None = None) -> Path:
    """Returns the memory file path. When conv_id is provided (non-admin session), uses it as the
    filename so each conversation window gets its own file. Falls back to date-based naming."""
    base = memory_dir if memory_dir is not None else MEMORY_DIR
    if conv_id:
        return base / f"{conv_id}.md"
    date_str = datetime.now().strftime("%m-%d-%y")
    return base / f"{date_str}.md"


def load_user_facts(memory_dir: Path) -> str:
    """Load persistent cross-session facts about a user from facts.json, formatted for injection."""
    facts_file = memory_dir / "facts.json"
    if not facts_file.exists():
        return ""
    try:
        data = json.loads(facts_file.read_text(encoding="utf-8"))
        if not data:
            return ""
        lines = ["## Known User Context"]
        for topic, info in data.items():
            fact = info.get("fact", "")
            if isinstance(fact, list):
                lines.append(f"- **{topic}**: {', '.join(str(f) for f in fact)}")
            else:
                lines.append(f"- **{topic}**: {fact}")
        return "\n".join(lines)
    except Exception:
        return ""

def _check_path(memory_file: Path, base: Path) -> None:
    """Guard against directory traversal — file must stay under base. Case-insensitive on Windows."""
    resolved = str(memory_file.resolve())
    base_resolved = str(base.resolve())
    import os
    if not (resolved + os.sep).lower().startswith((base_resolved + os.sep).lower()):
        raise ValueError(f"Path traversal blocked: {memory_file}")

def load_recent_memory(max_chars: int = 4000, session_id: str | None = None, memory_dir: Path | None = None, conv_id: str | None = None) -> str:
    """Loads the recent conversation history, ensuring whole messages are preserved."""
    base = memory_dir if memory_dir is not None else MEMORY_DIR
    memory_file = get_current_memory_file(session_id, memory_dir=memory_dir, conv_id=conv_id)
    _check_path(memory_file, base)

    if not memory_file.exists():
        return "*No previous conversation history for today.*"

    with open(memory_file, "r", encoding="utf-8") as f:
        content = f.read()

    if len(content) > max_chars:
        blocks = content.split("\n\n---\n\n")

        recent_blocks = []
        current_length = 0

        for block in reversed(blocks):
            if not block.strip():
                continue

            block_len = len(block) + 9
            if current_length + block_len > max_chars and recent_blocks:
                break

            recent_blocks.insert(0, block)
            current_length += block_len

        truncated_history = "\n\n---\n\n".join(recent_blocks)
        return f"...[OLDER MEMORY TRUNCATED]...\n\n{truncated_history}\n\n---\n\n"

    return content

def append_to_memory(role: str, text: str, session_id: str | None = None, memory_dir: Path | None = None, conv_id: str | None = None):
    """Appends a new message to the conversation memory file and runs cleanup."""
    base = memory_dir if memory_dir is not None else MEMORY_DIR
    base.mkdir(parents=True, exist_ok=True)
    memory_file = get_current_memory_file(session_id, memory_dir=memory_dir, conv_id=conv_id)
    _check_path(memory_file, base)

    with open(memory_file, "a", encoding="utf-8") as f:
        f.write(f"### {role}\n{text}\n\n---\n\n")

    try:
        _get_episode_store().record(_Episode(
            content=f"{role}: {text[:1000]}",
            type="conversation",
            session_id=session_id or "",
        ))
    except Exception:
        pass

    truncate_active_session(memory_file)

    global _last_prune
    now = time.time()
    if now - _last_prune > 86400:
        prune_old_sessions()
        _last_prune = now

def clear_memory(session_id: str | None = None, memory_dir: Path | None = None, conv_id: str | None = None):
    """Wipes the memory file for the given session (or the global file)."""
    base = memory_dir if memory_dir is not None else MEMORY_DIR
    memory_file = get_current_memory_file(session_id, memory_dir=memory_dir, conv_id=conv_id)
    _check_path(memory_file, base)
    if memory_file.exists():
        try:
            memory_file.unlink()
            return True
        except Exception as e:
            print(f"⚠️ Failed to clear memory: {e}")
            return False
    return True

def truncate_memory_at(index: int, session_id: str | None = None, memory_dir: Path | None = None, conv_id: str | None = None):
    """Truncates the memory file at the given block index (deletes index and everything after)."""
    base = memory_dir if memory_dir is not None else MEMORY_DIR
    memory_file = get_current_memory_file(session_id, memory_dir=memory_dir, conv_id=conv_id)
    _check_path(memory_file, base)

    if not memory_file.exists():
        return False

    try:
        with open(memory_file, "r", encoding="utf-8") as f:
            content = f.read()

        blocks = content.split("\n\n---\n\n")
        if blocks and not blocks[-1].strip():
            blocks.pop()

        if index < 0 or index >= len(blocks):
            return False

        new_blocks = blocks[:index]

        if not new_blocks:
            memory_file.unlink()
        else:
            new_content = "\n\n---\n\n".join(new_blocks) + "\n\n---\n\n"
            with open(memory_file, "w", encoding="utf-8") as f:
                f.write(new_content)
        return True
    except Exception as e:
        print(f"⚠️ Failed to truncate memory: {e}")
        return False