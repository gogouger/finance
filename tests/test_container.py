import json
import http.client
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from base64 import urlsafe_b64encode


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_production_container_serves_the_finance_shell():
    image = f"finance-test:{uuid.uuid4().hex[:10]}"
    container = f"finance-test-{uuid.uuid4().hex[:10]}"
    volume = f"finance-test-{uuid.uuid4().hex[:10]}"
    port = _unused_port()
    key = urlsafe_b64encode(b"1" * 32).decode()

    subprocess.run(["docker", "build", "-t", image, "."], check=True)
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container,
                "--publish",
                f"127.0.0.1:{port}:8080",
                "--env",
                f"FINANCE_ENCRYPTION_KEY={key}",
                "--volume",
                f"{volume}:/data",
                image,
            ],
            check=True,
            capture_output=True,
        )

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                    assert json.load(response) == {"service": "finance", "status": "ok"}
                break
            except (
                urllib.error.URLError,
                http.client.RemoteDisconnected,
                ConnectionResetError,
                TimeoutError,
            ):
                time.sleep(0.1)
        else:
            raise AssertionError("production container did not become healthy")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as response:
            assert "Your money, explained." in response.read().decode()

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready") as response:
            first_fingerprint = json.load(response)["installation_fingerprint"]

        subprocess.run(["docker", "restart", container], check=True, capture_output=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=0.5) as response:
                    second_fingerprint = json.load(response)["installation_fingerprint"]
                break
            except (
                urllib.error.URLError,
                http.client.RemoteDisconnected,
                ConnectionResetError,
                TimeoutError,
            ):
                time.sleep(0.1)
        else:
            raise AssertionError("production container did not recover after restart")

        assert second_fingerprint == first_fingerprint
    finally:
        subprocess.run(["docker", "rm", "--force", container], check=False, capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], check=False, capture_output=True)
        subprocess.run(["docker", "image", "rm", image], check=False, capture_output=True)
