import json
import asyncio
import threading

from pathlib import Path
from datetime import datetime

from app.skills.sdk import wade_tool
from app.skills.indexing.indexer import queue_file_for_index
from app.core.personality import PersonalityManager, SKIP_FILES
from app.memory.md_patcher import _patch_md_field

WORKSPACE_DIR = Path.home() / ".wade" / "workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
MAX_TOOL_OUTPUT_LENGTH = 1500

memory_file_lock = threading.Lock()

MEMORY_JSON = WORKSPACE_DIR / "memory.json"
MEMORY_MD = WORKSPACE_DIR / "MEMORY.md"
PROTECTED_FILES = {"BOOTSTRAP.md", "HEARTBEAT.md"}

personality_manager = PersonalityManager(chroma_client=None)

def _load_memory_db() -> dict:
    """Helper to safely load the JSON memory."""
    if not MEMORY_JSON.exists():
        return {}
    try:
        return json.loads(MEMORY_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def _save_memory_db(data: dict):
    """Saves the JSON database AND auto-generates the human-readable MEMORY.md."""
    MEMORY_JSON.write_text(json.dumps(data, indent=4), encoding="utf-8")

    md_content = "# LONG-TERM MEMORY\n\n*Curated facts automatically synced from the core memory database.*\n\n"

    if not data:
        md_content += "*Memory is currently empty.*"
    else:
        for topic, info in data.items():
            md_content += f"### {topic}\n- {info['fact']}\n- *(Updated: {info['timestamp']})*\n\n"

    MEMORY_MD.write_text(md_content, encoding="utf-8")

def read_core_memory() -> str:
    """Reads the structured MEMORY database and returns a human-friendly summary of all stored facts."""
    blocks = []

    with memory_file_lock:
        data = _load_memory_db()

    if data:
        mem_lines = ["--- [MEMORY.md : LONG-TERM FACTS] ---"]
        for topic, info in data.items():
            mem_lines.append(f"[{topic}]: {info['fact']} (Last updated: {info['timestamp']})")
        blocks.append("\n".join(mem_lines))

    if not blocks:
        return "Memory is currently empty."

    return "\n\n".join(blocks)

@wade_tool(
    name="manage_knowledge_base",
    description="Read, write, and manage files in the W.A.D.E. workspace and structured memory facts.",
    risk="medium",
    category="memory",
    parameters={
        "action": {
            "type": "string",
            "description": (
                "Operation to perform. One of: list_files | read_full_file | "
                "rewrite_full_file | append_to_file | store_fact | delete_fact | patch_field"
            ),
        },
        "target": {
            "type": "string",
            "description": (
                "Workspace .md filename (e.g. 'USER.md'). "
                "Use 'MEMORY.md' for structured facts. Omit for list_files."
            ),
        },
        "topic": {
            "type": "string",
            "description": "Fact topic/key — required for store_fact and delete_fact on MEMORY.md.",
        },
        "sentinel": {
            "type": "string",
            "description": (
                "Exact string to find and replace in the target file. "
                "Required for patch_field. Must match character-for-character."
            ),
        },
        "content": {
            "type": "string",
            "description": "Content to write — required for store_fact, rewrite_full_file, and append_to_file.",
        },
    },
    required_params=["action"],
    reversible=False,
    instructions=(
        "list_files: no other params needed. "
        "read_full_file: target=filename. "
        "rewrite_full_file/append_to_file: target=filename, content=text. "
        "store_fact: target='MEMORY.md', topic=key, content=value. "
        "delete_fact: target='MEMORY.md', topic=key. "
        "patch_field: target=filename, sentinel=exact_string_to_find, content=replacement_string. "
        "Replaces the first occurrence of sentinel in target with content. "
        "Returns 'not found' message if sentinel is absent — call is safe to retry with corrected sentinel. "
        "MEMORY.md is a structured JSON database — only store_fact/delete_fact modify it; "
        "read_full_file/rewrite_full_file/append_to_file/patch_field are for plain .md workspace files only. "
        "Do not use patch_field on MEMORY.md."
    ),
)
async def manage_knowledge_base(action: str = "", target: str = "", topic: str = "", content: str = "", sentinel: str = "") -> str:
    """Async tool handler for the LLM to execute intelligent workspace modifications."""
    def _execute_op():
        nonlocal target
        
        with memory_file_lock:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if action == "list_files":
                try:
                    files = sorted(p.name for p in WORKSPACE_DIR.glob("*.md"))
                    if not files:
                        return "No .md files found in workspace."
                    lines = ["Available workspace .md files:"]
                    for fname in files:
                        tag = " [protected]" if fname in PROTECTED_FILES else ""
                        tag = " [skip]" if fname in SKIP_FILES else tag
                        lines.append(f"  - {fname}{tag}")
                    return "\n".join(lines)
                except Exception as e:
                    return f"Error listing files: {e}"

            if not target and action in ("store_fact", "delete_fact"):
                target = "MEMORY.md"

            if not target:
                return "Error: 'target' is required for this action. For structured memory facts use action='store_fact' with target='MEMORY.md'."

            filename = target if target.endswith(".md") else f"{target}.md"

            if filename in PROTECTED_FILES:
                return f"Error: {filename} is a system-internal file and cannot be modified directly."

            if filename == "MEMORY.md":
                if action in ["read_full_file", "rewrite_full_file", "append_to_file"]:
                    return (
                        "System Error: MEMORY.md is a structured database. "
                        "Use 'store_fact' or 'delete_fact' to modify it, or 'read_full_file' on a different file."
                    )

                data = _load_memory_db()
                topic_key = topic.strip().title()

                if action == "store_fact":
                    if not topic_key or not content.strip():
                        return "Schema Error: 'topic' and 'content' cannot be empty when storing a memory."
                    if topic_key in data and data[topic_key]["fact"] == content:
                        return f"No update needed. Memory for '{topic_key}' already contains this exact fact."
                    data[topic_key] = {"fact": content, "timestamp": timestamp}
                    _save_memory_db(data)
                    personality_manager.invalidate_cache("MEMORY.md")
                    queue_file_for_index(str(MEMORY_MD))
                    return f"Memory stored successfully under topic '{topic_key}'."

                elif action == "delete_fact":
                    if topic_key in data:
                        del data[topic_key]
                        _save_memory_db(data)
                        personality_manager.invalidate_cache("MEMORY.md")
                        queue_file_for_index(str(MEMORY_MD))
                        return f"Successfully deleted memory topic '{topic_key}'."
                    return f"Error: Topic '{topic_key}' not found in memory."

                return f"System Error: Invalid action '{action}' for MEMORY.md. Use 'store_fact' or 'delete_fact'."

            if action in ["store_fact", "delete_fact"]:
                return f"System Error: '{action}' is only for MEMORY.md. Use read/rewrite/append for {filename}."

            file_path = WORKSPACE_DIR / filename

            if action == "read_full_file":
                if not file_path.exists():
                    return f"File not found: {filename}"
                try:
                    content_out = file_path.read_text(encoding="utf-8").strip()
                    return content_out if content_out else f"{filename} exists but is empty."
                except Exception as e:
                    return f"Error reading {filename}: {e}"

            elif action == "rewrite_full_file":
                if not content.strip():
                    return f"Warning: Cannot overwrite {filename} with empty content."
                try:
                    verb = "Created" if not file_path.exists() else "Rewrote"
                    file_path.write_text(content, encoding="utf-8")
                    personality_manager.invalidate_cache(filename)
                    queue_file_for_index(str(file_path))
                    return f"{verb} {filename} successfully. Changes are live."
                except Exception as e:
                    return f"Error writing {filename}: {e}"

            elif action == "append_to_file":
                if not content.strip():
                    return "Warning: Cannot append empty content."
                try:
                    existing = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
                    new_content = (existing + "\n\n" + content) if existing else content
                    file_path.write_text(new_content.strip() + "\n", encoding="utf-8")
                    personality_manager.invalidate_cache(filename)
                    queue_file_for_index(str(file_path))
                    return f"Successfully appended content to {filename}."
                except Exception as e:
                    return f"Error appending to {filename}: {e}"

            elif action == "patch_field":
                if not sentinel.strip():
                    return "Schema Error: 'sentinel' is required for patch_field."
                if not content.strip():
                    return "Schema Error: 'content' is required for patch_field."
                if not file_path.exists():
                    return f"File not found: {filename}"
                patched = _patch_md_field(file_path, sentinel, content)
                if patched:
                    personality_manager.invalidate_cache(filename)
                    queue_file_for_index(str(file_path))
                    return f"Patched {filename}: replaced sentinel successfully."
                return f"Sentinel not found in {filename} — no changes made."

            return f"System Error: Invalid action '{action}'."

    return await asyncio.to_thread(_execute_op)

if __name__ == "__main__":
    async def run_test():
        print("--- TEST 1: List files ---")
        print(await manage_knowledge_base("list_files"))

        print("\n--- TEST 2: Store a KV memory fact ---")
        print(await manage_knowledge_base("store_fact", target="MEMORY.md", topic="User Name", content="Prefers to go by the nickname Ducky."))

        print("\n--- TEST 3: Read USER.md ---")
        print(await manage_knowledge_base("read_full_file", target="USER.md"))

        print("\n--- TEST 4: Create a custom file ---")
        print(await manage_knowledge_base("rewrite_full_file", target="PROJECTS.md", content="# Active Projects\n\n- W.A.D.E. — local AI agent\n"))

        print("\n--- TEST 5: Read the new custom file ---")
        print(await manage_knowledge_base("read_full_file", target="PROJECTS.md"))

    asyncio.run(run_test())