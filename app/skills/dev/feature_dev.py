import re
import time
from pathlib import Path

from app.skills.registry import register_tool

from app.skills.registry import register_tool

@register_tool("feature_dev")
async def feature_dev(feature_name: str, description: str, target_dir: str = ".") -> str:
    """Initializes a new feature development blueprint file in the target directory. This creates a structured markdown file to guide W.A.D.E through the exploration, design, implementation, and review phases of developing a new feature. The blueprint serves as both a plan and a log for the feature's development lifecycle."""
    safe_name = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        feature_name
    ).strip("_").lower()

    blueprint_dir = Path(target_dir) / ".wade" / "blueprints"

    try:
        blueprint_dir.mkdir(parents=True, exist_ok=True)

        blueprint_path = (
            blueprint_dir / f"{safe_name}_blueprint.md"
        )

        template = f"""# Feature Blueprint: {feature_name}

> Created: {time.strftime('%Y-%m-%d %H:%M:%S')}
> Status: 🔴 IN PROGRESS
> Target Directory: {target_dir}

---

## 1. Objective

{description}

---

## 2. Repository Exploration

- [ ] Identify related files and modules.
- [ ] Document existing implementation patterns.
- [ ] Review interfaces, dependencies, and conventions.
- [ ] Identify potential architectural constraints.

---

## 3. Architecture Plan

- [ ] Define files to create.
- [ ] Define files to modify.
- [ ] Define interfaces and data flow.
- [ ] Identify migration or compatibility concerns.
- [ ] Define validation/testing strategy.

---

## 4. Incremental Implementation Log

- [ ] Step 1
- [ ] Step 2
- [ ] Step 3

---

## 5. Validation & Review

- [ ] Review final repository diff.
- [ ] Run tests and validation tooling.
- [ ] Verify implementation consistency.
- [ ] Finalize documentation if required.

---

## 6. Completion Summary

- [ ] Summarize completed work.
- [ ] Document known limitations or follow-ups.
"""

        blueprint_path.write_text(
            template,
            encoding="utf-8"
        )

        return (
            "STATUS: success\n\n"
            f"Blueprint initialized at:\n"
            f"{blueprint_path}\n\n"
            "NEXT STEPS:\n"
            "1. Review the generated blueprint.\n"
            "2. Complete repository exploration.\n"
            "3. Define the architecture plan.\n"
            "4. Begin incremental implementation.\n"
        )

    except Exception as e:
        return (
            "STATUS: error\n\n"
            f"Failed to initialize blueprint:\n{e}"
        )