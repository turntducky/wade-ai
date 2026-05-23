import sys
import types
import argparse

from typing import Any
from pathlib import Path
from unittest.mock import MagicMock, patch

for _mod in ("whisper", "torch", "onnxruntime", "sounddevice",
             "openwakeword", "kokoro_onnx"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

if "app.services.voice" not in sys.modules:
    _voice_stub: Any = types.ModuleType("app.services.voice")
    _voice_stub.get_voice_service = MagicMock()

def _make_wade_home(tmp_path: Path) -> Path:
    """Create a realistic ~/.wade layout under tmp_path."""
    (tmp_path / "config.yaml").write_text("llm: {}")
    (tmp_path / "data" / "voices").mkdir(parents=True)
    (tmp_path / "workspace").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / "monitors").mkdir()
    (tmp_path / "gateway.log").write_text("log")
    (tmp_path / "tasks.db").write_text("")
    (tmp_path / "gateway.pid").write_text("1234")
    (tmp_path / "models.lock").write_text("")
    return tmp_path

def _patch_duck_home(tmp_path: Path):
    """Redirect all config path constants to tmp_path."""
    return patch.multiple(
        "app.core.config",
        DUCK_HOME=tmp_path,
        CONFIG_FILE=tmp_path / "config.yaml",
        DATA_DIR=tmp_path / "data",
        WORKSPACE_DIR=tmp_path / "workspace",
        LOG_FILE=tmp_path / "gateway.log",
        MODEL_LOCK_FILE=tmp_path / "models.lock",
        PID_FILE=tmp_path / "gateway.pid",
        SKILLS_DIR=tmp_path / "skills",
        TASKS_DB_PATH=tmp_path / "tasks.db",
        MONITORS_USER_DIR=tmp_path / "monitors",
    )

def test_manifest_includes_existing_paths(tmp_path):
    _make_wade_home(tmp_path)
    with _patch_duck_home(tmp_path):
        with patch("app.cli.sys.platform", "linux"):  # skip schtasks on non-Windows
            from app.cli import _build_uninstall_manifest
            manifest = _build_uninstall_manifest()

    paths = [item["path"] for item in manifest if item["path"] is not None]
    item_types = {item["type"] for item in manifest}

    assert tmp_path / "config.yaml" in paths
    assert tmp_path / "workspace" in paths
    assert tmp_path / "memory" in paths
    assert tmp_path in paths
    assert "file" in item_types
    assert "dir" in item_types

def test_manifest_excludes_missing_paths(tmp_path):
    (tmp_path / "config.yaml").write_text("llm: {}")
    with _patch_duck_home(tmp_path):
        with patch("app.cli.sys.platform", "linux"):
            from app.cli import _build_uninstall_manifest
            manifest = _build_uninstall_manifest()

    paths = [item["path"] for item in manifest]
    assert tmp_path / "config.yaml" in paths
    assert tmp_path / "workspace" not in paths
    assert tmp_path / "memory" not in paths

def test_confirm_returns_true_on_y(capsys):
    from app.cli import _confirm_uninstall
    manifest = [
        {"label": "Config file",        "path": Path("/fake/.wade/config.yaml"), "type": "file"},
        {"label": "WADE_GodMode_Start", "path": None,                            "type": "task"},
    ]
    with patch("builtins.input", return_value="y"):
        result = _confirm_uninstall(manifest, remove_package=True)
    assert result is True
    captured = capsys.readouterr()
    assert "config.yaml" in captured.out
    assert "WADE_GodMode_Start" in captured.out
    assert "wade-ai" in captured.out

def test_confirm_returns_false_on_n(capsys):
    from app.cli import _confirm_uninstall
    manifest = [{"label": "Config file", "path": Path("/fake/.wade/config.yaml"), "type": "file"}]
    with patch("builtins.input", return_value="n"):
        result = _confirm_uninstall(manifest, remove_package=False)
    assert result is False

def test_confirm_returns_false_on_empty_input(capsys):
    from app.cli import _confirm_uninstall
    manifest = [{"label": "Config file", "path": Path("/fake/.wade/config.yaml"), "type": "file"}]
    with patch("builtins.input", return_value=""):
        result = _confirm_uninstall(manifest, remove_package=False)
    assert result is False

def test_execute_removes_files_and_dirs(tmp_path):
    from app.cli import _execute_uninstall

    f = tmp_path / "config.yaml"
    f.write_text("content")
    d = tmp_path / "workspace"
    d.mkdir()
    (d / "file.txt").write_text("hi")

    manifest = [
        {"label": "Config file", "path": f, "type": "file"},
        {"label": "Workspace",   "path": d, "type": "dir"},
    ]
    _execute_uninstall(manifest, remove_package=False)

    assert not f.exists()
    assert not d.exists()

def test_execute_removes_scheduled_task():
    from app.cli import _execute_uninstall

    manifest = [{"label": "WADE_GodMode_Start", "path": None, "type": "task"}]

    with patch("app.cli.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        _execute_uninstall(manifest, remove_package=False)

    mock_run.assert_called_once_with(
        ["schtasks", "/delete", "/tn", "WADE_GodMode_Start", "/f"],
        capture_output=True,
    )

def test_execute_runs_pip_uninstall_when_requested():
    from app.cli import _execute_uninstall

    with patch("app.cli.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        _execute_uninstall([], remove_package=True)

    assert mock_run.call_count == 2
    mock_run.assert_any_call(
        [sys.executable, "-m", "pip", "uninstall", "wade-ai", "-y"],
        capture_output=True,
        text=True,
    )

def test_execute_prints_manual_instruction_on_pip_failure(capsys):
    from app.cli import _execute_uninstall

    with patch("app.cli.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        _execute_uninstall([], remove_package=True)

    captured = capsys.readouterr()
    assert "pip uninstall wade-ai" in captured.err

def _fake_args():
    return argparse.Namespace()

def test_handle_uninstall_stops_daemon_if_running():
    from app.cli import handle_uninstall

    with patch("app.cli.is_running", return_value=True), \
         patch("app.cli.stop_daemon") as mock_stop, \
         patch("app.cli._build_uninstall_manifest", return_value=[]), \
         patch("builtins.input", return_value="n"):
        handle_uninstall(_fake_args())

    mock_stop.assert_called_once()

def test_handle_uninstall_exits_early_if_nothing_to_remove(capsys):
    from app.cli import handle_uninstall

    with patch("app.cli.is_running", return_value=False), \
         patch("app.cli._build_uninstall_manifest", return_value=[]):
        handle_uninstall(_fake_args())

    captured = capsys.readouterr()
    assert "Nothing to remove" in captured.out

def test_handle_uninstall_cancels_on_no_confirm(capsys):
    from app.cli import handle_uninstall

    manifest = [{"label": "Config file", "path": Path("/fake/config.yaml"), "type": "file"}]
    with patch("app.cli.is_running", return_value=False), \
         patch("app.cli._build_uninstall_manifest", return_value=manifest), \
         patch("builtins.input", return_value="n"), \
         patch("app.cli._execute_uninstall") as mock_exec:
        handle_uninstall(_fake_args())

    mock_exec.assert_not_called()
    captured = capsys.readouterr()
    assert "cancelled" in captured.out

def test_handle_uninstall_executes_on_confirm():
    from app.cli import handle_uninstall

    manifest = [{"label": "Config file", "path": Path("/fake/config.yaml"), "type": "file"}]
    with patch("app.cli.is_running", return_value=False), \
         patch("app.cli._build_uninstall_manifest", return_value=manifest), \
         patch("builtins.input", side_effect=iter(["n", "y"])), \
         patch("app.cli._execute_uninstall") as mock_exec:
        handle_uninstall(_fake_args())

    mock_exec.assert_called_once_with(manifest, False)