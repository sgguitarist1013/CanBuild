import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx
import pytest


@pytest.fixture(scope="session")
def app_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, SENTRY_DSN="")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=Path(__file__).resolve().parents[1], env=env)
    try:
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError("Test server exited before becoming healthy")
            try:
                if httpx.get(url + "/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError("Test server did not become healthy")
        yield url
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args
