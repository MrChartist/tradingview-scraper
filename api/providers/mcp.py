"""A small client for MCP servers that speak Streamable HTTP (JSON-RPC 2.0 over POST).

Only what Tickvale needs: initialise a session, call a tool, read its JSON answer. No extra dependency.
The session is created on first use and re-created if the server forgets it.
"""
import json
import logging
import threading
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("market_terminal.mcp")
PROTOCOL_VERSION = "2025-03-26"


class McpError(Exception):
    """The server answered with an error, or the tool reported one."""


class McpClient:
    def __init__(self, url: str, client_name: str = "tickvale", client_version: str = "1.0", timeout: float = 30):
        self.url = url
        self.timeout = timeout
        self._info = {"name": client_name, "version": client_version}
        self._http = requests.Session()
        self._session_id: Optional[str] = None
        self._lock = threading.Lock()
        self._next_id = 0

    # ── wire format ──────────────────────────────────────────────
    @staticmethod
    def _read(resp: requests.Response) -> Optional[dict]:
        """The answer is either plain JSON or a server-sent-event stream carrying one JSON message."""
        if "event-stream" in resp.headers.get("content-type", ""):
            messages = [json.loads(line[5:].strip()) for line in resp.text.splitlines() if line.startswith("data:")]
            return messages[-1] if messages else None
        return resp.json() if resp.text.strip() else None

    def _post(self, body: dict, timeout: float, with_session: bool = True) -> requests.Response:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if with_session and self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return self._http.post(self.url, headers=headers, json=body, timeout=timeout)

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _initialise(self, timeout: float) -> None:
        resp = self._post({"jsonrpc": "2.0", "id": self._new_id(), "method": "initialize",
                           "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": self._info}},
                          timeout, with_session=False)
        resp.raise_for_status()
        message = self._read(resp) or {}
        if "error" in message:
            raise McpError(f"initialize failed: {message['error']}")
        self._session_id = resp.headers.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout)

    # ── public ───────────────────────────────────────────────────
    def call(self, tool: str, arguments: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        """Run a tool and return what it produced (parsed JSON when the text is JSON, else the text)."""
        timeout = timeout or self.timeout
        for attempt in (1, 2):
            with self._lock:
                if self._session_id is None:
                    self._initialise(timeout)
                body = {"jsonrpc": "2.0", "id": self._new_id(), "method": "tools/call",
                        "params": {"name": tool, "arguments": arguments or {}}}
            resp = self._post(body, timeout)
            if resp.status_code in (400, 404) and attempt == 1:       # the session was dropped: start over once
                logger.info("MCP session lost on %s; reconnecting", self.url)
                with self._lock:
                    self._session_id = None
                continue
            resp.raise_for_status()
            message = self._read(resp) or {}
            if "error" in message:
                raise McpError(str(message["error"].get("message", message["error"])))
            result = message.get("result", {})
            content = result.get("content") or []
            text = content[0].get("text", "") if content else ""
            if result.get("isError"):
                raise McpError(text[:300] or "the tool reported an error")
            try:
                return json.loads(text)
            except ValueError:
                return text
        raise McpError("could not establish a session")      # pragma: no cover
