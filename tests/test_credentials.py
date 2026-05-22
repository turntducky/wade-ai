import json
import threading

def _reset(creds_file):
    """Helper: reset class cache and point module at a tmp file."""
    import app.core.credentials as m
    m.CredentialsManager._cache = None
    m.CREDENTIALS_FILE = creds_file

def test_get_returns_none_when_file_missing(tmp_path):
    import app.core.credentials as m
    _reset(tmp_path / "credentials.json")
    assert m.CredentialsManager.get("notion") is None

def test_save_then_get_roundtrip(tmp_path):
    import app.core.credentials as m
    _reset(tmp_path / "credentials.json")
    m.CredentialsManager.save("notion", {"token": "secret_abc"})
    assert m.CredentialsManager.get("notion") == {"token": "secret_abc"}

def test_save_merges_multiple_services(tmp_path):
    import app.core.credentials as m
    creds_file = tmp_path / "credentials.json"
    _reset(creds_file)
    m.CredentialsManager.save("notion", {"token": "n_tok"})
    m.CredentialsManager.save("blink", {"username": "u@example.com"})
    data = json.loads(creds_file.read_text())
    assert "notion" in data
    assert "blink" in data
    assert data["notion"]["token"] == "n_tok"

def test_get_returns_deep_copy(tmp_path):
    import app.core.credentials as m
    _reset(tmp_path / "credentials.json")
    m.CredentialsManager.save("notion", {"token": "abc"})
    result = m.CredentialsManager.get("notion")
    assert result is not None
    result["token"] = "mutated"
    fresh_result = m.CredentialsManager.get("notion")
    assert fresh_result is not None
    assert fresh_result["token"] == "abc"

def test_all_returns_all_services(tmp_path):
    import app.core.credentials as m
    _reset(tmp_path / "credentials.json")
    m.CredentialsManager.save("notion", {"token": "n"})
    m.CredentialsManager.save("blink",  {"username": "b"})
    result = m.CredentialsManager.all()
    assert set(result.keys()) == {"notion", "blink"}

def test_get_returns_none_for_unknown_service(tmp_path):
    import app.core.credentials as m
    _reset(tmp_path / "credentials.json")
    m.CredentialsManager.save("notion", {"token": "x"})
    assert m.CredentialsManager.get("unknown_service") is None

def test_atomic_write_creates_no_leftover_tmp(tmp_path):
    import app.core.credentials as m
    creds_file = tmp_path / "credentials.json"
    _reset(creds_file)
    m.CredentialsManager.save("notion", {"token": "x"})
    assert not (tmp_path / "credentials.tmp").exists()
    assert creds_file.exists()

def test_thread_safety(tmp_path):
    import app.core.credentials as m
    _reset(tmp_path / "credentials.json")
    errors = []

    def _write(i):
        try:
            m.CredentialsManager.save(f"svc_{i}", {"val": i})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    data = m.CredentialsManager.all()
    assert len(data) == 10