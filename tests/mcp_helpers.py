"""Shared helpers for MCP server tests.

Provides a small in-process HTTP upstream (ThreadingHTTPServer) that records
requests and serves canned JSON, plus a spec-dir builder that points at it —
so MCP e2e tests exercise the real ``execute_pipeline`` (auth chain, request
assembly, file bridge, response parse) over real HTTP without external
network.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPOS_SPEC = """\
description: Repos
path: repos
methods:
  list:
    http: {method: GET}
    params:
      query:
        - name: page
          type: int
          default: 1
    output:
      items_path: items
      fields:
        - name: name
  create:
    http: {method: POST}
    params:
      body:
        - name: name
          type: string
          required: true
  upload:
    http: {method: POST}
    body_type: multipart
    params:
      body:
        - name: file
          type: file
          required: true
"""


class _Handler(BaseHTTPRequestHandler):
    """Records the last request and replies with canned JSON."""

    records: list[dict[str, Any]] = []
    list_payload: Any = {"items": [{"name": "repo-a"}, {"name": "repo-b"}], "total": 2}

    def _record_and_reply(self, payload: Any, status: int = 200) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        _Handler.records.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {k: v for k, v in self.headers.items()},
                "body": raw,
            }
        )
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._record_and_reply(self.list_payload)

    def do_POST(self) -> None:
        self._record_and_reply({"ok": True})

    def log_message(self, *args: Any) -> None:  # silence request logging
        pass


class MockUpstream:
    """A throwaway HTTP upstream serving canned JSON on a free port."""

    def __init__(self, list_payload: Any = None) -> None:
        if list_payload is not None:
            _Handler.list_payload = list_payload
        _Handler.records = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def records(self) -> list[dict[str, Any]]:
        return _Handler.records

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def write_spec(
    tmp_path: Path,
    base_url: str,
    name: str = "mock-svc",
    auth: dict[str, Any] | None = None,
) -> Path:
    """Write a spec directory targeting *base_url* and return its path."""
    spec = Path(tmp_path) / "spec"
    spec.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", "version: '1.0'", "server:", f"  base_url: {base_url}"]
    if auth:
        import yaml

        dump = yaml.safe_dump(auth, allow_unicode=True, sort_keys=False).strip()
        lines.append("auth:")
        lines.append("  " + dump.replace("\n", "\n  "))
    (spec / "_auth.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (spec / "repos.yaml").write_text(REPOS_SPEC, encoding="utf-8")
    return spec


def write_auth_spec(tmp_path: Path, base_url: str, env_var: str = "MOCK_TOKEN") -> Path:
    """Write a spec with an env→inject auth chain (token read from env var)."""
    auth = {
        "steps": [
            {"name": "token", "type": "env", "config": {"name": env_var}},
            {
                "name": "inject",
                "type": "inject",
                "config": {
                    "source": "token",
                    "into": "header",
                    "name": "Authorization",
                    "prefix": "Bearer ",
                },
            },
        ]
    }
    return write_spec(tmp_path, base_url, name="auth-svc", auth=auth)
