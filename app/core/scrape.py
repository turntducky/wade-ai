import json
import subprocess

from app.core.config import DATA_DIR

def scrape_hf():
    """List locally available Ollama models and cache to disk."""
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().splitlines()[1:]  # skip header row
        models = []
        for line in lines:
            parts = line.split()
            if parts:
                name = parts[0]
                models.append({
                    "id": name,
                    "category": _categorize(name),
                    "downloads": 0,
                    "is_moe": False,
                })
    except Exception as e:
        print(f"Ollama list failed: {e}")
        models = []

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "hf_models.json", "w") as f:
        json.dump(models, f, indent=2)

def _categorize(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ["vision", "llava", "vl"]):
        return "vision"
    if any(x in n for x in ["embed", "bge", "gte", "nomic"]):
        return "embedding"
    if any(x in n for x in ["r1", "deepseek-r"]):
        return "reasoning"
    if any(x in n for x in ["coder", "code", "python"]):
        return "coding"
    return "chat"