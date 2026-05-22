import pytest

from app.agents.planner import _is_simple

@pytest.mark.parametrize("goal", [
    "Hey!",
    "hi",
    "Hello there",
    "good morning",
    "thanks",
    "Thank you",
    "ok",
    "sure",
    "lol",
    "What is the weather today?",
    "How are you doing?",
    "Can you help me?",
    "run a system check",
    "search for cats",
    "get the current time",
    "check the weather",
])
def test_is_simple_returns_true(goal):
    assert _is_simple(goal) is True

@pytest.mark.parametrize("goal", [
    "delete my files",
    "rm -rf /",
    "wipe the database",
    "reset everything",
    "drop the users table",
    "truncate logs",
    "kill the process",
    "terminate the server",
    "sudo apt install curl",
    "chmod 777 file.py",
    "chown root myfile",
    "env PATH=bad cmd",
    "overwrite the config",
    "format the disk",
    "Delete all",
    "reset db",
    "kill it",
])
def test_is_simple_returns_false_on_danger_token(goal):
    assert _is_simple(goal) is False

@pytest.mark.parametrize("goal", [
    "Please check my calendar and book a flight to London",
    "Build me a website with authentication, a dashboard, and analytics",
])
def test_is_simple_returns_false_on_complex_intent(goal):
    assert _is_simple(goal) is False