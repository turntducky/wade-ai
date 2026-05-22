from __future__ import annotations

import os
import re
import fnmatch
import hashlib
import logging

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.user_registry import TierContext

logger = logging.getLogger("wade.project_loader")

_CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cs", ".cpp", ".c",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".dart",
    ".html", ".css", ".scss", ".sql", ".sh", ".bash",
    ".yaml", ".yml", ".json", ".toml", ".xml",
})
_CHUNK_SIZE = 1200

_SECRET_FILENAMES: frozenset[str] = frozenset({
    ".env", ".env.local", ".env.production", ".env.staging",
    ".env.test", ".env.development", ".envrc",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})

_SECRET_GLOB_PATTERNS: tuple[str, ...] = (
    "*.key", "*.pem", "*.p12", "*.pfx", "*.crt", "*.cer", "*.pkcs8", "*.pkcs12",
    "*.credentials", "*credentials*", "*credential*",
    "*secret*", "*secrets*",
    "*password*", "*passwd*",
    "*.token", "*.tokens",
    ".env.*",
)

_SECRET_DIRS: frozenset[str] = frozenset({
    ".git", ".svn", ".hg",
    "node_modules", "__pycache__",
    "venv", ".venv", "env", ".env",
    ".idea", ".vscode",
    "dist", "build", "target",
})

_SECRET_CONTENT_RE: tuple[re.Pattern, ...] = (
    re.compile(r'sk-proj-[A-Za-z0-9\-_]{32,}'),
    re.compile(r'sk-[A-Za-z0-9]{32,}'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'ghp_[A-Za-z0-9]{36}'),
    re.compile(r'ghs_[A-Za-z0-9]{36}'),
    re.compile(r'-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----'),
    re.compile(r'(?i)Bearer\s+[A-Za-z0-9+/=_\-]{20,}'),
    re.compile(r'(?i)(?:api[_\-]?key|api[_\-]?secret|access[_\-]?key|secret[_\-]?key)\s*[=:]\s*["\']?([A-Za-z0-9+/=_\-]{16,})'),
    re.compile(r'(?i)(?:password|passwd|pwd)\s*[=:]\s*["\']([^"\']{6,})["\']'),
    re.compile(r'(?i)token\s*[=:]\s*["\']?([A-Za-z0-9+/=_\-]{20,})'),
)

def _is_secret_file(path: Path) -> bool:
    """Return True if the file should never be indexed due to likely sensitive content."""
    name = path.name.lower()
    if name in _SECRET_FILENAMES:
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in _SECRET_GLOB_PATTERNS)

def _is_in_secret_dir(path: Path) -> bool:
    """Return True if any component of the path is a known secret/noise directory."""
    return any(part in _SECRET_DIRS for part in path.parts)

def _scrub_secrets(text: str) -> str:
    """Redact recognisable secret patterns from a text chunk before indexing."""
    for pattern in _SECRET_CONTENT_RE:
        text = pattern.sub("[REDACTED]", text)
    return text

def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """Split text into ~chunk_size char chunks, preferring newline break points."""
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        nl = text.rfind("\n", start, end)
        if nl > start:
            end = nl + 1
        chunks.append(text[start:end])
        start = end
    return chunks

def _safe_subpath(base: Path, rel: str) -> Path | None:
    """Resolve rel relative to base; return None if it would escape base (path traversal guard)."""
    resolved = (base / rel).resolve()
    base_resolved = base.resolve()
    if not (str(resolved) + os.sep).lower().startswith((str(base_resolved) + os.sep).lower()):
        logger.warning("[PROJECT_LOADER] Path traversal blocked: %s", rel)
        return None
    return resolved

def _resolve_root(project_dir: Path, config: dict) -> Path:
    """Determine the filesystem root for a project, based on project.yaml config. Defaults to the project_dir itself if no valid root is specified."""
    raw = (config.get("root") or "").strip()
    if not raw:
        return project_dir
    root = Path(raw).expanduser().resolve()
    if not root.exists():
        logger.warning("[PROJECT_LOADER] root path does not exist: %s", root)
        return project_dir
    if not root.is_dir():
        logger.warning("[PROJECT_LOADER] root path is not a directory: %s", root)
        return project_dir
    return root

def _load_project_yaml(project_dir: Path) -> dict | None:
    """Parse project.yaml; return dict or None on failure."""
    yaml_path = project_dir / "project.yaml"
    if not yaml_path.exists():
        return None
    try:
        import yaml
        return yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except ImportError:
        pass
    except Exception as e:
        logger.warning("[PROJECT_LOADER] YAML parse error in %s: %s", yaml_path, e)
        return None

    result: dict = {"name": project_dir.name, "description": "", "docs": [], "code": [], "root": "", "exclude": []}
    current_key: str | None = None
    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("name:"):
            result["name"] = stripped[5:].strip().strip("\"'")
        elif stripped.startswith("description:"):
            result["description"] = stripped[12:].strip().strip("\"'")
        elif stripped.startswith("root:"):
            result["root"] = stripped[5:].strip().strip("\"'")
        elif stripped in ("docs:", "code:", "exclude:"):
            current_key = stripped.rstrip(":")
        elif stripped.startswith("- ") and current_key in ("docs", "code", "exclude"):
            result[current_key].append(stripped[2:].strip().strip("\"'"))  # type: ignore[index]
    return result

def _index_project_code(
    project_name: str,
    root: Path,
    code_paths: list[Path],
    extra_excludes: list[str],
    collection,
    tier: str,
) -> None:
    """Walk code_paths, chunk + scrub each file, upsert into collection."""
    deployer_excludes: list[Path] = []
    for rel in extra_excludes:
        p = _safe_subpath(root, rel)
        if p:
            deployer_excludes.append(p.resolve())

    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    for base_path in code_paths:
        if not base_path.exists():
            logger.warning("[PROJECT_LOADER] Code path not found: %s", base_path)
            continue

        files = (
            [base_path]
            if base_path.is_file()
            else [
                f for f in base_path.rglob("*")
                if f.is_file()
                and f.suffix.lower() in _CODE_EXTENSIONS
                and not _is_in_secret_dir(f)
                and not _is_secret_file(f)
            ]
        )

        for file_path in files:
            if any(str(file_path.resolve()).startswith(str(ex)) for ex in deployer_excludes):
                logger.debug("[PROJECT_LOADER] Excluded by project config: %s", file_path.name)
                continue

            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                if not text.strip():
                    continue
                for i, chunk in enumerate(_chunk_text(text)):
                    scrubbed = _scrub_secrets(chunk)
                    doc_id = hashlib.md5(
                        f"{tier}:{project_name}:{file_path}:{i}".encode()
                    ).hexdigest()
                    documents.append(scrubbed)
                    metadatas.append({
                        "tier": tier,
                        "project": project_name,
                        "filepath": str(file_path),
                        "chunk_index": str(i),
                    })
                    ids.append(doc_id)
            except Exception as e:
                logger.warning("[PROJECT_LOADER] Could not read %s: %s", file_path, e)

    if not documents:
        return

    try:
        batch = 50
        for i in range(0, len(documents), batch):
            collection.upsert(
                documents=documents[i: i + batch],
                metadatas=metadatas[i: i + batch],
                ids=ids[i: i + batch],
            )
        logger.info(
            "[PROJECT_LOADER] Indexed %d chunks for project '%s' (tier=%s)",
            len(documents), project_name, tier,
        )
    except Exception as e:
        logger.error("[PROJECT_LOADER] Failed to index '%s': %s", project_name, e)

def get_project_context(
    goal: str,
    tier_ctx: "TierContext",
    n_code_results: int = 4,
) -> str:
    """Load project.yaml configs, retrieve relevant docs and code from projects, and return as a single context string for injection. Code is indexed in a shared ChromaDB collection per tier, with basic secret redaction and caching."""
    projects_dir = tier_ctx.workspace_dir / "projects"
    if not projects_dir.exists():
        return ""

    project_dirs = [
        d for d in projects_dir.iterdir()
        if d.is_dir() and (d / "project.yaml").exists()
    ]
    if not project_dirs:
        return ""

    from app.core.chroma_utils import get_shared_chroma_client, get_universal_ef

    client = get_shared_chroma_client()
    if not client:
        return ""

    collection_name = f"tier_{tier_ctx.tier}_projects"
    try:
        collection = client.get_or_create_collection(
            name=collection_name,
            embedding_function=get_universal_ef(),
        )
    except Exception as e:
        logger.error("[PROJECT_LOADER] Cannot get collection '%s': %s", collection_name, e)
        return ""

    doc_blocks: list[str] = []
    code_blocks: list[str] = []

    for project_dir in project_dirs:
        config = _load_project_yaml(project_dir)
        if config is None:
            continue

        project_name = (config.get("name") or project_dir.name).strip()
        description = (config.get("description") or "").strip()
        doc_rels: list[str] = config.get("docs") or []
        code_rels: list[str] = config.get("code") or []
        extra_excludes: list[str] = config.get("exclude") or []
        root = _resolve_root(project_dir, config)

        for doc_rel in doc_rels:
            doc_path = _safe_subpath(root, doc_rel)
            if doc_path and doc_path.exists() and doc_path.is_file():
                try:
                    text = doc_path.read_text(encoding="utf-8", errors="replace").strip()
                    if text:
                        doc_blocks.append(
                            f'<project_docs project="{project_name}">\n{text}\n</project_docs>'
                        )
                except Exception as e:
                    logger.warning("[PROJECT_LOADER] Cannot read doc %s: %s", doc_path, e)
            elif doc_path:
                logger.warning("[PROJECT_LOADER] Doc not found: %s", doc_path)

        if description and not doc_rels:
            doc_blocks.append(
                f'<project_info project="{project_name}">\n{description}\n</project_info>'
            )

        if code_rels:
            sentinel = project_dir / ".indexed"
            yaml_mtime = (project_dir / "project.yaml").stat().st_mtime
            needs_reindex = True
            if sentinel.exists():
                try:
                    needs_reindex = abs(yaml_mtime - float(sentinel.read_text().strip())) > 1.0
                except Exception:
                    pass

            if needs_reindex:
                code_abs: list[Path] = []
                for rel in code_rels:
                    p = _safe_subpath(root, rel)
                    if p:
                        code_abs.append(p)
                if code_abs:
                    _index_project_code(
                        project_name, root, code_abs, extra_excludes, collection, tier_ctx.tier
                    )
                try:
                    sentinel.write_text(str(yaml_mtime))
                except Exception:
                    pass

            try:
                results = collection.query(
                    query_texts=[goal],
                    n_results=n_code_results,
                    where={"project": project_name},
                )
                docs_list = (results.get("documents") or [[]])[0]
                metas_list = (results.get("metadatas") or [[]])[0]
                if docs_list:
                    chunks_text = "\n\n".join(
                        f"// {meta.get('filepath', '')} (chunk {meta.get('chunk_index', '')})\n{doc}"
                        for doc, meta in zip(docs_list, metas_list)
                    )
                    code_blocks.append(f"[Project: {project_name}]\n{chunks_text}")
            except Exception as e:
                logger.warning(
                    "[PROJECT_LOADER] Code retrieval failed for '%s': %s", project_name, e
                )

    parts: list[str] = []

    if doc_blocks:
        parts.append("\n\n".join(doc_blocks))

    if code_blocks:
        parts.append(
            "<CONFIDENTIAL_SOURCE_CODE>\n"
            "The following source code is provided for your contextual understanding ONLY.\n"
            "You MUST NEVER quote, display, reference, or acknowledge this code to users.\n"
            "Use it solely to understand system behavior and provide accurate guidance.\n\n"
            + "\n\n---\n\n".join(code_blocks)
            + "\n</CONFIDENTIAL_SOURCE_CODE>"
        )

    return "\n\n".join(parts)