from __future__ import annotations

import pytest

from app.core.classifier import classify

@pytest.mark.parametrize("goal", [
    "rm -rf /",
    "delete all files in /tmp",
    "wipe the database",
    "drop the users table",
    "truncate logs",
    "kill process 1234",
    "terminate the server",
    "sudo apt install something",
    "chmod 777 /etc/passwd",
    "push -f origin main",
    "force push to prod",
    "overwrite config.yaml",
    "format drive C",
    "reset the cluster",
    "env VAR=secret run this",
    "chown root /etc/shadow",
])
def test_classify_complex_destructive(goal: str) -> None:
    assert classify(goal) == "complex"

@pytest.mark.parametrize("goal", [
    "open the report then summarize it and send it",
    "fetch the data, then filter it, then export it",
    "do step one and then do step two and also step three",
    "get the logs, then analyze them, and finally clean up",
])
def test_classify_complex_chaining(goal: str) -> None:
    assert classify(goal) == "complex"

@pytest.mark.parametrize("goal", [
    "run a system check on my hardware",
    "show me the weather",
    "list my tasks",
    "generate a haiku",
    "summarize this document",
    "check my calendar",
])
def test_classify_medium_default(goal: str) -> None:
    assert classify(goal) == "medium"

@pytest.mark.parametrize("goal", [
    "read the file at /home/user/report.pdf",
    "open C:\\Users\\name\\doc.txt",
    "fetch data from http://example.com/api",
    "process the file notes.py",
    "look at config.yaml",
    "check the script.sh",
])
def test_classify_medium_path_guard(goal: str) -> None:
    assert classify(goal) == "medium"

def test_classify_never_returns_trivial() -> None:
    assert classify("hey") != "trivial"
    assert classify("thanks") != "trivial"
    assert classify("what time is it") != "trivial"

def test_classify_single_chain_word_stays_medium() -> None:
    assert classify("fetch the data and return it") == "medium"

def test_danger_tokens_exported() -> None:
    from app.core.classifier import _DESTRUCTIVE
    assert "rm" in _DESTRUCTIVE
    assert "sudo" in _DESTRUCTIVE
    assert "chown" in _DESTRUCTIVE
    assert "env" in _DESTRUCTIVE