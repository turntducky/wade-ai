import pytest

from pathlib import Path
from unittest.mock import AsyncMock, patch

try:
    from app.skills.dev.dev_file import (
        _resolve_language,
        _extract_package,
        _format_report,
        dev_file,
    )
except ImportError:
    pytest.skip("dev_file helpers not yet implemented", allow_module_level=True)

def test_resolve_language_explicit_param():
    assert _resolve_language("foo.unknown", "python") == "python"

def test_resolve_language_from_py_extension():
    assert _resolve_language("/some/path/script.py", "") == "python"

def test_resolve_language_from_js_extension():
    assert _resolve_language("/some/path/app.js", "") == "node"

def test_resolve_language_unknown_extension_returns_none():
    assert _resolve_language("/some/path/file.rb", "") is None

def test_resolve_language_explicit_overrides_extension():
    assert _resolve_language("/some/path/file.js", "bash") == "bash"

def test_extract_package_simple():
    err = "ModuleNotFoundError: No module named 'pytz'"
    assert _extract_package(err) == "pytz"

def test_extract_package_submodule():
    err = "ModuleNotFoundError: No module named 'requests.adapters'"
    assert _extract_package(err) == "requests"

def test_extract_package_no_match_returns_none():
    assert _extract_package("SyntaxError: invalid syntax") is None

def test_format_report_contains_all_sections():
    result = _format_report("success", "hello world", "/tmp/t.py", "print('hi')")
    assert "STATUS: success" in result
    assert "EXECUTION OUTPUT:" in result
    assert "hello world" in result
    assert "ACTUAL FILE CONTENT (/tmp/t.py):" in result
    assert "print('hi')" in result

def test_format_report_empty_output_shows_no_output():
    result = _format_report("success", "", "/tmp/t.py", "x = 1")
    assert "No output." in result

def test_format_report_numbers_lines():
    result = _format_report("success", "", "/tmp/t.py", "a\nb\nc")
    assert "   1 | a" in result
    assert "   2 | b" in result
    assert "   3 | c" in result

def test_format_report_empty_content_shows_placeholder():
    result = _format_report("success", "", "/tmp/t.py", "")
    assert "(empty file)" in result

@pytest.mark.asyncio
async def test_dev_file_writes_and_runs_python(tmp_path):
    path = str(tmp_path / "hello.py")
    result = await dev_file(path, "print('wade works')")
    assert "STATUS: success" in result
    assert "wade works" in result
    assert f"ACTUAL FILE CONTENT ({path}):" in result
    assert "print('wade works')" in result
    assert Path(path).read_text(encoding="utf-8") == "print('wade works')"

@pytest.mark.asyncio
async def test_dev_file_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "nested" / "deep" / "script.py")
    result = await dev_file(path, "print('nested')")
    assert "STATUS: success" in result
    assert Path(path).exists()

@pytest.mark.asyncio
async def test_dev_file_unknown_language_returns_error(tmp_path):
    path = str(tmp_path / "script.rb")
    result = await dev_file(path, "puts 'hello'")
    assert "ERROR" in result
    assert "language" in result.lower()

@pytest.mark.asyncio
async def test_dev_file_actual_content_is_from_disk_not_memory(tmp_path):
    path = str(tmp_path / "self_mutate.py")
    code = f"open(r'{path}', 'w').write('# mutated by execution')\nprint('done')"
    result = await dev_file(path, code)
    assert "STATUS: success" in result
    assert "# mutated by execution" in result
    assert "done" in result

@pytest.mark.asyncio
async def test_dev_file_runtime_error(tmp_path):
    path = str(tmp_path / "broken.py")
    result = await dev_file(path, "raise ValueError('intentional')")
    assert "STATUS: runtime_error" in result
    assert "intentional" in result
    assert f"ACTUAL FILE CONTENT ({path}):" in result

@pytest.mark.asyncio
async def test_dev_file_pip_install_success(tmp_path):
    path = str(tmp_path / "mod_test.py")
    code = "import fake_pkg\nprint('ok')"

    run_side_effects = [
        ("ModuleNotFoundError: No module named 'fake_pkg'", 1),
        ("ok", 0),
    ]
    pip_result = (True, "Successfully installed fake_pkg")

    with patch("app.skills.dev.dev_file._run_file",
               new=AsyncMock(side_effect=run_side_effects)), \
         patch("app.skills.dev.dev_file._pip_install",
               new=AsyncMock(return_value=pip_result)):
        result = await dev_file(path, code)

    assert "STATUS: fixed_after_install" in result
    assert f"ACTUAL FILE CONTENT ({path}):" in result
    assert "import fake_pkg" in result

@pytest.mark.asyncio
async def test_dev_file_pip_install_failure(tmp_path):
    path = str(tmp_path / "mod_fail.py")
    code = "import ghost_pkg\nprint('ok')"

    run_result = ("ModuleNotFoundError: No module named 'ghost_pkg'", 1)
    pip_result = (False, "ERROR: Could not find a version that satisfies ghost_pkg")

    with patch("app.skills.dev.dev_file._run_file",
               new=AsyncMock(return_value=run_result)), \
         patch("app.skills.dev.dev_file._pip_install",
               new=AsyncMock(return_value=pip_result)):
        result = await dev_file(path, code)

    assert "STATUS: install_failed" in result
    assert f"ACTUAL FILE CONTENT ({path}):" in result
    assert "ghost_pkg" in result

@pytest.mark.asyncio
async def test_dev_file_pip_success_but_code_still_fails(tmp_path):
    path = str(tmp_path / "still_broken.py")
    code = "import fake_pkg\nraise RuntimeError('still broken')"

    run_side_effects = [
        ("ModuleNotFoundError: No module named 'fake_pkg'", 1),
        ("RuntimeError: still broken", 1),
    ]
    pip_result = (True, "Successfully installed fake_pkg")

    with patch("app.skills.dev.dev_file._run_file",
               new=AsyncMock(side_effect=run_side_effects)), \
         patch("app.skills.dev.dev_file._pip_install",
               new=AsyncMock(return_value=pip_result)):
        result = await dev_file(path, code)

    assert "STATUS: runtime_error" in result
    assert f"ACTUAL FILE CONTENT ({path}):" in result

@pytest.mark.asyncio
async def test_dev_file_module_not_found_unparseable_skips_pip(tmp_path):
    path = str(tmp_path / "weird.py")
    code = "raise ModuleNotFoundError('custom message without standard pattern')"

    run_result = ("ModuleNotFoundError: custom message without standard pattern", 1)

    with patch("app.skills.dev.dev_file._run_file",
               new=AsyncMock(return_value=run_result)), \
         patch("app.skills.dev.dev_file._pip_install") as mock_pip:
        result = await dev_file(path, code)

    mock_pip.assert_not_called()
    assert "STATUS: runtime_error" in result
    assert f"ACTUAL FILE CONTENT ({path}):" in result