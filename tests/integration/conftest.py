"""Pytest configuration for integration tests - handles server startup/shutdown"""

import pytest
import subprocess
import sys
import time
import socket
import signal
import os
from pathlib import Path

# Import and disable rate limiter
import web_app.__main__ as main_module
from web_app.helpers import limiter

# Disable rate limiting for integration tests
limiter.enabled = False

# Port for the test server
TEST_PORT = 54321
TEST_BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


def wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    """Wait for server to become available"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            time.sleep(0.1)
    return False


def find_free_port() -> int:
    """Find a free port on localhost"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture(scope="session")
def server_url():
    """Start the Flask server for integration tests and return the base URL"""
    # Use a dynamic port to avoid conflicts
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    
    # Get the project root directory
    project_root = Path(__file__).parent.parent.parent
    
    # Start the server as a subprocess with a script that disables rate limiting
    env = os.environ.copy()

    # Create a startup script that disables rate limiting
    startup_script = f'''
import sys
sys.path.insert(0, "{project_root}")

# Import and disable rate limiter before importing other modules
import web_app.helpers
web_app.helpers.limiter.enabled = False

# Now run the app
from web_app.app import app
app.config["SESSION_COOKIE_SECURE"] = False

from web_app.__main__ import cli_start
cli_start(["--port", "{port}", "--debug"])
'''
    
    process = subprocess.Popen(
        [sys.executable, "-c", startup_script],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    
    # Wait for server to be ready
    if not wait_for_server("127.0.0.1", port, timeout=30.0):
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        output = b"\n".join(part for part in (stdout, stderr) if part).decode(
            "utf-8", errors="replace"
        )
        raise RuntimeError(f"Server failed to start on port {port}\n{output}")
    
    yield base_url
    
    # Cleanup: terminate the server
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
