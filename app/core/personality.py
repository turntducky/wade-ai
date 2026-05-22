import time
import chromadb

from typing import Any
from pathlib import Path

WORKSPACE_DIR = Path.home() / ".wade" / "workspace"
CHROMA_DB_DIR = Path.home() / ".wade" / "vector_store"

ALWAYS_INJECT_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "PROJECTS.md"]
TOOLS_INSTRUCTIONS_FILE = "TOOLS.md"

CORE_IDENTITY_FILES = ALWAYS_INJECT_FILES

SKIP_FILES = {
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "MEMORY.md",
}

_STATIC_FILES = {"SOUL.md", "IDENTITY.md", "TOOLS.md"}
_STATIC_CACHE_TTL   = 3600
_MUTABLE_CACHE_TTL  = 120
_WORKSPACE_QUERY_TTL = 30

_NO_CLIENT = object()

class PersonalityManager:
    def __init__(self, chroma_client: Any = _NO_CLIENT, workspace_dir: Path | None = None):
        self._cache: dict[str, tuple[float, str]] = {}
        self.cache_ttl = _MUTABLE_CACHE_TTL
        self._workspace_query_cache: dict[str, tuple[float, str]] = {}
        self._workspace_dir: Path = workspace_dir if workspace_dir is not None else WORKSPACE_DIR

        if chroma_client is _NO_CLIENT:
            try:
                self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
            except Exception:
                self.chroma_client = None
        else:
            self.chroma_client = chroma_client

        try:
            self.collection = self.chroma_client.get_collection(name="wade_core_workspace") if self.chroma_client else None
        except Exception:
            self.collection = None

    def _extract_chunk_index(self, meta: Any) -> int:
        """Safely extracts and casts the chunk_index from ChromaDB metadata."""
        if not meta:
            return 0
        
        val = meta.get("chunk_index", 0)
        
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
        
        return 0

    def _read_from_disk(self, filename: str) -> str:
        """Reads a workspace .md file directly from disk with a short TTL cache."""
        now = time.time()
        if filename in self._cache:
            cached_time, content = self._cache[filename]
            ttl = _STATIC_CACHE_TTL if filename in _STATIC_FILES else _MUTABLE_CACHE_TTL
            if now - cached_time < ttl:
                return content

        file_path = self._workspace_dir / filename
        if not file_path.exists():
            return ""

        try:
            content = file_path.read_text(encoding="utf-8").strip()
            from app.core.config import ConfigManager
            content = content.replace("{ASSISTANT_NAME}", ConfigManager.get_assistant_name())
            self._cache[filename] = (now, content)
            return content
        except Exception as e:
            return f"*[{filename} read error: {e}]*"

    def _read_from_vector_store(self, filename: str) -> str:
        """Reads and reconstructs a workspace .md file from vector store chunks, with a short TTL cache."""
        disk_content = self._read_from_disk(filename)
        if disk_content:
            return disk_content

        if not self.collection:
            return f"*[{filename} not found]*"

        try:
            results = self.collection.get(
                where={"filename": filename},
                include=["documents", "metadatas"]
            )
            
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []

            if not docs:
                return f"*[{filename} not found in vector store]*"

            indexed_chunks = sorted(
                [(self._extract_chunk_index(meta), doc) for doc, meta in zip(docs, metas)],
                key=lambda x: x[0]
            )
            return "".join(c[1] for c in indexed_chunks).strip()
        except Exception as e:
            return f"*[{filename} retrieval error: {e}]*"

    def invalidate_cache(self, filename: str | None = None):
        """Invalidates the cache for one file, or the entire cache if filename is None."""
        if filename:
            self._cache.pop(filename, None)
        else:
            self._cache.clear()

    def _get_bootstrap_context(self) -> str:
        """Reads BOOTSTRAP.md from disk if it exists and returns it wrapped in <onboarding_protocol> tags. This is used to inject first-run onboarding instructions with high priority."""
        bootstrap_path = self._workspace_dir / "BOOTSTRAP.md"
        if not bootstrap_path.exists():
            return ""
        try:
            content = bootstrap_path.read_text(encoding="utf-8").strip()
            if content:
                return f"\n\n<onboarding_protocol>\n{content}\n</onboarding_protocol>"
        except Exception:
            pass
        return ""

    def get_core_identity_context(self) -> str:
        """Returns the combined content of core identity files (SOUL.md, IDENTITY.md) wrapped in <core_directives> tags. These files are critical for the agent's self-concept and are always injected with highest priority."""
        blocks = []
        for filename in ALWAYS_INJECT_FILES:
            content = self._read_from_disk(filename)
            if content:
                clean_content = "\n".join([line for line in content.split('\n') if not line.startswith(f"# {filename}")])
                blocks.append(clean_content.strip())

        if not blocks:
            return ""

        base = "<core_directives>\n" + "\n\n".join(blocks) + "\n</core_directives>"

        bootstrap = self._get_bootstrap_context()
        return base + bootstrap if bootstrap else base

    def get_tools_instructions(self) -> str:
        """Returns the TOOLS.md execution instructions block. Only call when tools are present."""
        return self._read_from_disk(TOOLS_INSTRUCTIONS_FILE)

    def get_tool_context(self, query: str) -> str:
        """Uses the SkillRouter to find and return a context block of relevant tools for the query."""
        from app.skills.semantic_router import get_relevant_tools_context
        return get_relevant_tools_context(query, self.chroma_client)

    def get_relevant_workspace_context(self, query: str, n_results: int = 4) -> str:
        """Queries the vector store for relevant workspace documents based on the input query, excluding core identity files and any files in the SKIP_FILES set. Returns the combined content of the retrieved documents, wrapped in tags based on their type (system capabilities, known user facts, or general retrieved memory). If the vector store is unavailable or an error occurs, returns an empty string."""
        if not self.collection or not query.strip():
            return ""

        now = time.time()
        cached = self._workspace_query_cache.get(query)
        if cached and now - cached[0] < _WORKSPACE_QUERY_TTL:
            return cached[1]

        exclude = set(ALWAYS_INJECT_FILES) | SKIP_FILES
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where={"filename": {"$nin": list(exclude)}}
            )
            
            docs_result = results.get("documents")
            metas_result = results.get("metadatas")
            
            docs = docs_result[0] if docs_result else []
            metas = metas_result[0] if metas_result else []

            if not docs:
                return ""

            blocks = []
            seen: set[str] = set()
            for doc, meta in zip(docs, metas):
                fname = str(meta.get("filename", "unknown.md")) if meta else "unknown.md"

                if fname not in seen:
                    seen.add(fname)
                    if "TOOLS" in fname or "AGENTS" in fname:
                        blocks.append(f'<system_capabilities>\n{doc}\n</system_capabilities>')
                    elif "USER" in fname:
                        blocks.append(f'<known_user_facts>\n{doc}\n</known_user_facts>')
                    else:
                        blocks.append(f'<retrieved_memory>\n{doc}\n</retrieved_memory>')
                else:
                    blocks.append(doc)
            result = "\n\n".join(blocks)
            self._workspace_query_cache[query] = (now, result)
            return result
        except Exception:
            return ""