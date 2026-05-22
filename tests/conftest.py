import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )
