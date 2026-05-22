from app.services.model_router import ModelRouter, DEFAULT_ROUTING_TABLE, ModelRoute

def test_resolve_known_role():
    router = ModelRouter({"fast": "qwen2.5:3b", "planner": "qwen2.5:14b"})
    route = router.resolve("planner")
    assert isinstance(route, ModelRoute)
    assert route.model == "qwen2.5:14b"
    assert route.provider == "ollama"

def test_resolve_falls_back_to_fast_for_unknown_role():
    router = ModelRouter({"fast": "qwen2.5:3b", "tools": "qwen2.5:7b"})
    route = router.resolve("nonexistent_role")
    assert route.model == "qwen2.5:3b"

def test_resolve_falls_back_to_hardcoded_default_when_fast_missing():
    router = ModelRouter({"tools": "qwen2.5:7b"})
    route = router.resolve("nonexistent_role")
    assert route.model == "qwen2.5:3b"

def test_resolve_with_provider_prefix():
    router = ModelRouter({"chat": "openai/gpt-4o"})
    route = router.resolve("chat")
    assert route.provider == "openai"
    assert route.model == "gpt-4o"

def test_resolve_with_custom_default_provider():
    router = ModelRouter({"chat": "gpt-4o"}, default_provider="openai")
    route = router.resolve("chat")
    assert route.provider == "openai"
    assert route.model == "gpt-4o"

def test_default_routing_table_has_all_required_roles():
    required = {"planner", "reasoner", "tools", "code", "vision", "embeddings", "fast"}
    assert required.issubset(DEFAULT_ROUTING_TABLE.keys())

def test_from_config_merges_user_config_over_defaults(tmp_path, monkeypatch):
    import yaml
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump({
        "models": {"fast": "custom:1b"},
        "llm": {"provider": "gemini"}
    }))

    import app.core.config as cfg_module
    monkeypatch.setattr(cfg_module, "CONFIG_FILE", config_file)
    import app.core.config as c
    c.ConfigManager._cache = None

    router = ModelRouter.from_config()
    route = router.resolve("fast")
    assert route.model == "custom:1b"
    assert route.provider == "gemini"
    
    route_code = router.resolve("code")
    assert route_code.model == DEFAULT_ROUTING_TABLE["code"]
    assert route_code.provider == "gemini"
    
    c.ConfigManager._cache = None