from app.services.model_router import DEFAULT_ROUTING_TABLE, ModelRouter

def test_planner_routes_to_3b():
    """planner must use the 3B fast model, not the 14B from the stale duplicate key."""
    assert DEFAULT_ROUTING_TABLE["planner"] == "qwen2.5:3b"

def test_reasoner_routes_to_14b():
    assert DEFAULT_ROUTING_TABLE["reasoner"] == "qwen2.5:14b"

def test_fast_routes_to_3b():
    assert DEFAULT_ROUTING_TABLE["fast"] == "qwen2.5:3b"

def test_no_unexpected_14b_assignments():
    """Only reasoner should use 14b — planner must not."""
    for role, model in DEFAULT_ROUTING_TABLE.items():
        if role != "reasoner":
            assert "14b" not in model, (
                f"Role '{role}' unexpectedly routes to 14b model '{model}'"
            )

def test_all_required_roles_present():
    required = {"chat", "tools", "planner", "reasoner", "code", "fast", "vision", "embeddings"}
    assert required.issubset(DEFAULT_ROUTING_TABLE.keys())

def test_router_resolve_planner():
    router = ModelRouter(DEFAULT_ROUTING_TABLE)
    assert router.resolve("planner").model == "qwen2.5:3b"

def test_router_resolve_reasoner():
    router = ModelRouter(DEFAULT_ROUTING_TABLE)
    assert router.resolve("reasoner").model == "qwen2.5:14b"

def test_router_resolve_unknown_falls_back_to_fast():
    router = ModelRouter(DEFAULT_ROUTING_TABLE)
    result = router.resolve("nonexistent_role")
    assert result.model == DEFAULT_ROUTING_TABLE["fast"]