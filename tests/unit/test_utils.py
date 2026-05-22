def test_safe_truncate_under_limit():
    from app.core.utils import safe_truncate
    assert safe_truncate("hello", 100) == "hello"

def test_safe_truncate_over_limit():
    from app.core.utils import safe_truncate
    result = safe_truncate("a" * 200, 100)
    assert len(result) <= 130
    assert "..." in result

def test_safe_truncate_empty_string():
    from app.core.utils import safe_truncate
    assert safe_truncate("", 100) == ""

def test_safe_truncate_zero_max_length():
    from app.core.utils import safe_truncate
    assert safe_truncate("hello", 0) == ""

def test_safe_truncate_negative_max_length():
    from app.core.utils import safe_truncate
    assert safe_truncate("hello", -5) == ""

def test_safe_truncate_exact_boundary():
    from app.core.utils import safe_truncate
    text = "hello"
    assert safe_truncate(text, len(text)) == text

def test_safe_truncate_output_is_bounded():
    from app.core.utils import safe_truncate
    result = safe_truncate("a" * 500, 100)
    assert len(result) <= 130