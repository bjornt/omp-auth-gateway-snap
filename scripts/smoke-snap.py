#!/usr/bin/env python3
"""Exercise the installed Snap's broker and gateway with a real omp client.

For disposable CI runners only: writes a dummy broker credential and a systemd
endpoint override. No provider credentials or model downloads are needed.
"""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen


SNAP = "omp-auth-gateway"
BROKER = "http://127.0.0.1:8765"
GATEWAY = "http://127.0.0.1:14000"
PROMPT = "What is the result of this smoke test?"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def request(url, *, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers,
                  data=json.dumps(body).encode() if body is not None else None)
    with urlopen(req, timeout=5) as response:
        return json.load(response)


def wait_for(url):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            request(url)
            return
        except (URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError(f"Service did not become ready: {url}")


class MockOpenAI(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/models":
            self.server.errors.append(f"Unexpected discovery route: {self.path}")
            self.send_error(404)
            return
        body = json.dumps({"object": "list", "data": [{
            "id": "kimi-k2.7-code", "object": "model", "owned_by": "moonshot",
        }]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        error = None
        if self.path != "/v1/chat/completions":
            error = f"Unexpected upstream route: {self.path}"
        elif self.headers.get("Authorization") != f"Bearer {self.server.api_key}":
            error = "Gateway did not send the broker's dummy provider credential"
        elif body.get("model") != "kimi-k2.7-code" or not body.get("stream"):
            error = "Expected a streaming kimi-k2.7-code completion"
        elif not any(PROMPT in json.dumps(m.get("content"))
                     for m in body.get("messages", []) if m.get("role") == "user"):
            error = "Client prompt did not reach the upstream"
        if error:
            self.server.errors.append(error)
            self.send_error(400, error)
            return
        self.server.completions += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in [({"role": "assistant", "content": ""}, None),
                              ({"content": self.server.answer}, None),
                              ({}, "stop")]:
            chunk = {"id": "chatcmpl-snap-smoke", "object": "chat.completion.chunk",
                     "created": 1, "model": "kimi-k2.7-code",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, fmt, *args):
        print(f"mock-openai: {fmt % args}", flush=True)


def main():
    with ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAI) as server:
        server.api_key = f"dummy-snap-smoke-{secrets.token_hex(16)}"
        server.answer = f"snap-smoke-ok-{secrets.token_hex(16)}"
        server.completions = 0
        server.errors = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        override = Path(f"/etc/systemd/system/snap.{SNAP}.gateway-daemon.service.d/smoke.conf")
        try:
            wait_for(f"{BROKER}/v1/healthz")
            broker_token = run("sudo", f"{SNAP}.broker", "token",
                               capture_output=True).stdout.strip()
            snapshot = request(f"{BROKER}/v1/snapshot", token=broker_token)
            if snapshot["credentials"]:
                raise RuntimeError("Smoke test requires a fresh, empty broker vault")
            request(f"{BROKER}/v1/credential", token=broker_token, body={
                "provider": "moonshot",
                "credential": {"type": "api_key", "key": server.api_key},
            })
            with tempfile.TemporaryDirectory(prefix="snap-smoke-") as temp:
                work = Path(temp)
                config = work / "smoke.conf"
                # Moonshot's supported override applies to bundled models; the
                # generic OPENAI_BASE_URL fallback does not override them.
                config.write_text(
                    "[Service]\n"
                    f"Environment=MOONSHOT_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n"
                )
                run("sudo", "install", "-D", "-m", "0644", str(config), str(override))
                run("sudo", "systemctl", "daemon-reload")
                # Exercise the Snap's configuration hook, on a dedicated test port.
                run("sudo", "snap", "set", SNAP, "gateway.bind=127.0.0.1:14000")
                wait_for(f"{BROKER}/v1/healthz")
                wait_for(f"{GATEWAY}/healthz")
                gateway_token = run("sudo", SNAP, "token", capture_output=True).stdout.strip()
                agent = work / ".omp" / "agent"
                agent.mkdir(parents=True)
                (agent / "models.yml").write_text(json.dumps({"providers": {"moonshot": {
                    "baseUrl": GATEWAY,
                    "apiKey": gateway_token,
                    "transport": "pi-native",
                }}}))
                # Deliberately omit the broker token and provider key from the client.
                result = subprocess.run(
                    ["omp", "--print", "--model", "moonshot/kimi-k2.7-code", "--thinking", "off",
                     "--no-session", "--no-tools", "--no-lsp", "--no-extensions",
                     "--no-skills", "--no-rules", "--no-context-files", "--no-title", PROMPT],
                    cwd=work, env={"HOME": temp, "PATH": os.environ["PATH"], "TERM": "dumb",
                                   "PI_CODING_AGENT_DIR": str(agent)},
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=120, text=True,
                )
                print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
                result.check_returncode()
                if result.stdout.strip() != server.answer:
                    raise RuntimeError("omp did not return the mock's exact completion")
                if server.errors or server.completions != 1:
                    raise RuntimeError(f"Unexpected upstream requests: {server.errors}; "
                                       f"completions={server.completions}")
                print("Smoke passed: omp → confined gateway → broker credential → mock OpenAI")
        finally:
            if override.exists():
                run("sudo", "snap", "stop", f"{SNAP}.gateway-daemon")
                run("sudo", "rm", str(override))
                run("sudo", "systemctl", "daemon-reload")
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    main()
