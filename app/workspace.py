from pathlib import Path

from app.core.config import get_package_dir

SYSTEM_TEMPLATES = {"IDENTITY.md", "SOUL.md", "TOOLS.md"}
TIER_SYSTEM_FILES = {"SOUL.md", "IDENTITY.md"}
USER_OWNED_FILES = {
    "STRANGER_PERSONA.md",
    "users.yaml",
    "BUSINESS.md",
    "PROJECTS.md",
    "USER.md",
    "MEMORY.md",
    "HEARTBEAT.md",
}
TIERS = ("family", "friends", "guests", "strangers")

def _template_dir() -> Path:
    """Return the path to bundled personality templates (works after pip install)."""
    return get_package_dir() / "templates"

def load_templates() -> dict[str, str]:
    """Load all markdown templates from the bundled templates directory."""
    templates: dict[str, str] = {}
    tdir = _template_dir()

    if not tdir.exists():
        print(f"  Warning: template directory not found at {tdir}")
        return templates

    for file in tdir.glob("*.md"):
        templates[file.name] = file.read_text(encoding="utf-8")

    return templates

def _scaffold_file(dest: Path, src: Path, *, always_refresh: bool, label: str, results: dict[str, list[str]]) -> None:
    """Scaffold a single file from src to dest, respecting refresh rules and recording results."""
    if not src.exists():
        return
    content = src.read_text(encoding="utf-8")
    if always_refresh:
        dest.write_text(content, encoding="utf-8")
        results["updated"].append(label)
    elif dest.exists():
        results["skipped"].append(label)
    else:
        dest.write_text(content, encoding="utf-8")
        results["created"].append(label)

def _scaffold_tier(tier: str, tdir: Path, wade_home: Path, results: dict) -> None:
    """Scaffold a single tier's workspace under ~/.wade/tiers/<tier>/."""
    tier_dest = wade_home / "tiers" / tier
    tier_dest.mkdir(parents=True, exist_ok=True)
    (tier_dest / "memory").mkdir(exist_ok=True)
    (tier_dest / "projects").mkdir(exist_ok=True)

    tier_src = tdir / "tiers" / tier
    if not tier_src.exists():
        return

    for src_file in tier_src.glob("*.md"):
        dest_file = tier_dest / src_file.name
        always_refresh = src_file.name in TIER_SYSTEM_FILES
        _scaffold_file(
            dest=dest_file,
            src=src_file,
            always_refresh=always_refresh,
            label=f"tiers/{tier}/{src_file.name}",
            results=results,
        )

    projects_src = tier_src / "projects"
    if projects_src.exists():
        for proj_src in projects_src.iterdir():
            if not proj_src.is_dir():
                continue
            proj_dest = tier_dest / "projects" / proj_src.name
            proj_dest.mkdir(parents=True, exist_ok=True)
            for src_file in proj_src.iterdir():
                if src_file.is_file():
                    _scaffold_file(
                        dest=proj_dest / src_file.name,
                        src=src_file,
                        always_refresh=False,
                        label=f"tiers/{tier}/projects/{proj_src.name}/{src_file.name}",
                        results=results,
                    )

def generate_cognitive_architecture(verbose: bool = False) -> None:
    """Bootstrap the entire workspace with system and tier templates, respecting user ownership rules."""
    wade_home = Path.home() / ".wade"
    workspace_dir = wade_home / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "memory").mkdir(exist_ok=True)

    tdir = _template_dir()
    results: dict[str, list[str]] = {"created": [], "updated": [], "skipped": []}

    if not tdir.exists():
        if verbose:
            print(f"  Warning: template directory not found at {tdir}")
    else:
        for src_file in tdir.glob("*.md"):
            dest_file = workspace_dir / src_file.name
            always_refresh = src_file.name in SYSTEM_TEMPLATES
            _scaffold_file(
                dest=dest_file,
                src=src_file,
                always_refresh=always_refresh,
                label=src_file.name,
                results=results,
            )

        _scaffold_file(
            dest=wade_home / "users.yaml",
            src=tdir / "users.yaml",
            always_refresh=False,
            label="users.yaml",
            results=results,
        )

        for tier in TIERS:
            _scaffold_tier(tier, tdir, wade_home, results)

    if verbose:
        for f in results["created"]:
            print(f"    created    {f}")
        for f in results["updated"]:
            print(f"    refreshed  {f}")
        for f in results["skipped"]:
            print(f"    exists     {f}")

def ensure_workspace_exists() -> None:
    """Bootstrap the workspace silently (used by daemon, server, and CLI commands)."""
    generate_cognitive_architecture(verbose=False)

if __name__ == "__main__":
    generate_cognitive_architecture(verbose=True)