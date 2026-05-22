from __future__ import annotations

from typing import Dict, Optional

from app.services.discovery import SIZE_LADDER, FIXED_TAG_MODELS

async def _pull_with_fallback(manager, model: str) -> Optional[str]:
    """Attempt to pull the specified model, with fallbacks for unavailable sizes."""
    try:
        await manager.ensure_model_pulled(model)
        return model
    except RuntimeError:
        pass

    if ":" in model:
        family, tag = model.rsplit(":", 1)
        if family not in FIXED_TAG_MODELS:
            start = next((i for i, (_, t) in enumerate(SIZE_LADDER) if t == tag), None)
            if start is not None:
                for _, smaller_tag in SIZE_LADDER[start + 1 :]:
                    candidate = f"{family}:{smaller_tag}"
                    print(f"    → {tag} unavailable, trying {candidate}...", flush=True)
                    try:
                        await manager.ensure_model_pulled(candidate)
                        return candidate
                    except RuntimeError:
                        continue

    family = model.split(":")[0] if ":" in model else model
    if family != model:
        print(f"    → falling back to default tag for {family}...", flush=True)
        try:
            await manager.ensure_model_pulled(family)
            return family
        except RuntimeError:
            pass

    return None

async def pull_optimal_models(suite: Dict[str, str]) -> Dict[str, str]:
    """Given a mapping of roles to model specifications, attempts to pull each model using the provided manager. If a specified model is unavailable, it tries smaller variants (if tagged) or falls back to the family default. Returns a mapping of roles to the actual models that were successfully pulled (which may differ from the input if fallbacks were used). Logs the process and any issues encountered."""
    from app.services.ollama_manager import ollama_manager

    pulled: Dict[str, str] = {}
    for role, model in suite.items():
        model = str(model).strip()
        if not model:
            print(f"  [{role}] no model specified — skipping")
            continue

        print(f"  [{role}] {model}", flush=True)
        result = await _pull_with_fallback(ollama_manager, model)
        if result:
            pulled[role] = result
            if result != model:
                print(f"  ✔  [{role}] pulled as {result}")
            else:
                print(f"  ✔  [{role}] ready")
        else:
            pulled[role] = model
            print(f"  !  [{role}] could not pull '{model}' — check connectivity")

    return pulled