"""Keep the original library's test job working.

The API, SDK and socket tests need FastAPI, uvicorn and websockets. The older "Python application" workflow
installs only the scraper library's own requirements (and runs on Python 3.8), so when those packages are
missing these files are skipped instead of failing to load. The "CI" workflow installs everything and runs them.
"""
import importlib.util

API_TESTS = [
    "test_api.py", "test_v1.py", "test_client.py", "test_socket.py",
    "test_ws_client.py", "test_providers.py", "test_examples.py",
]

collect_ignore = []
if any(importlib.util.find_spec(name) is None for name in ("fastapi", "uvicorn", "websockets", "starlette")):
    collect_ignore = list(API_TESTS)
