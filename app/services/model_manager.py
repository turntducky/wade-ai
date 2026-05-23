from app.core.config import ConfigManager
from app.services.installer import pull_optimal_models
from app.services.discovery import async_generate_optimal_suite

def _infer_model_family(suite: dict) -> str:
    chat = str(suite.get("chat", "")).lower()
    for family in ("qwen", "llama", "mistral", "phi", "gemma", "deepseek"):
        if family in chat:
            return family
    return "default"

def _suite_to_routing_table(suite: dict) -> dict:
    """Map discovery suite roles → model_router routing table keys."""
    chat      = suite.get("chat",      "llama3.2:3b")
    coding    = suite.get("coding",    chat)
    reasoning = suite.get("reasoning", chat)
    embedding = suite.get("embedding", "nomic-embed-text")
    vision    = suite.get("vision",    "llava:7b")
    fast      = suite.get("fast",      chat)
    return {
        "chat":       chat,
        "tools":      chat,
        "planner":    chat,
        "reasoner":   reasoning,
        "code":       coding,
        "vision":     vision,
        "fast":       fast,
        "embeddings": embedding,
    }

async def fit_and_install_models() -> None:
    print("Discovering optimal Ollama models for your hardware...")
    suite = await async_generate_optimal_suite()

    if not suite:
        print("Model discovery returned an empty suite. Config not updated.")
        return

    print("\nTarget suite:")
    for role, model in suite.items():
        print(f"  {role:<12} → {model}")
    print()

    actual_suite = await pull_optimal_models(suite)

    config = ConfigManager.get()
    config["active_suite"]  = actual_suite
    config["models"]        = _suite_to_routing_table(actual_suite)
    config["model_family"]  = _infer_model_family(actual_suite)
    ConfigManager.save(config)

    print(f"\nModel family: {config['model_family']}")
    print("Run 'wade start' to launch W.A.D.E. with the updated models.")