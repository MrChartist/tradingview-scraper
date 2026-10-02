"""The contract, as data: JSON Schema for every operation, and an AsyncAPI document for the WebSocket.

Any product, in any language, can read these to generate or check its own client. They are built from the
live registry, so operations added by plugins appear automatically.
"""
from typing import Any, Dict, Optional

from api import operations as ops
from api.socket import CHANNELS

FRAMES_FROM_SERVER = {
    "hello": "Sent once on connect: operations, channels, sources, limits, a worked example.",
    "result": "Answer to a request: {type, id, op, data, meta}.",
    "error": "Something went wrong: {type, id|null, code, message, hint?, status?}. The connection stays open.",
    "subscribed": "Confirms a subscription: {type, id, channel, symbols?, rejected?, resolved?, key?}.",
    "unsubscribed": "Confirms an unsubscribe.",
    "quote": "A live price: {type: 'quote', data: <quote>}.",
    "update": "A pushed list: {type: 'update', channel, key, data, meta}.",
    "heartbeat": "Every 20 seconds.",
    "pong": "Reply to {op: 'ping'}.",
}


def _params_schema(model, ref_template: Optional[str] = None) -> Dict[str, Any]:
    kwargs = {"by_alias": True}
    if ref_template:
        kwargs["ref_template"] = ref_template
    return model.model_json_schema(**kwargs)


def build_schema(version: str) -> Dict[str, Any]:
    """Plain JSON Schema per operation."""
    return {
        "version": version,
        "operations": {
            o.name: {"summary": o.summary, "public": o.public, "params": _params_schema(o.model)}
            for o in sorted(ops.REGISTRY.values(), key=lambda o: o.name)
        },
        "channels": {c.name: {"summary": c.summary, "params": c.params} for c in CHANNELS.values()},
        "frames_from_server": FRAMES_FROM_SERVER,
        "request_frame": {
            "type": "object", "required": ["op"],
            "properties": {"id": {"description": "Any text or number; the answer repeats it."},
                           "op": {"type": "string"}, "params": {"type": "object"}},
        },
    }


def build_asyncapi(version: str, base_url: str = "") -> Dict[str, Any]:
    """AsyncAPI 2.6 description of /v1/ws."""
    schemas: Dict[str, Any] = {}
    messages: Dict[str, Any] = {}
    requests = []
    for o in sorted(ops.REGISTRY.values(), key=lambda o: o.name):
        schema = _params_schema(o.model, "#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        name = "Request_" + o.name.replace(".", "_")
        messages[name] = {
            "name": name, "summary": o.summary,
            "payload": {"type": "object", "required": ["op"], "additionalProperties": False,
                        "properties": {"id": {}, "op": {"const": o.name}, "params": schema}},
        }
        requests.append({"$ref": f"#/components/messages/{name}"})
    for kind, text in FRAMES_FROM_SERVER.items():
        name = "Server_" + kind
        messages[name] = {"name": name, "summary": text,
                          "payload": {"type": "object", "required": ["type"], "properties": {"type": {"const": kind}}}}
    host = base_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") or "ws://localhost:8000"
    return {
        "asyncapi": "2.6.0",
        "info": {"title": "Tickvale WebSocket", "version": version,
                 "description": "One connection to ask for market data and receive live quotes and lists. "
                                "Read the 'hello' message after connecting. See docs/WEBSOCKET.md."},
        "servers": {"default": {"url": host, "protocol": "wss" if host.startswith("wss") else "ws",
                                "description": "Send your key as X-API-Key, 'Authorization: Bearer', or ?api_key=."}},
        "channels": {"/v1/ws": {
            "description": "The only WebSocket endpoint.",
            "publish": {"summary": "What a client sends", "message": {"oneOf": requests}},
            "subscribe": {"summary": "What the server sends",
                          "message": {"oneOf": [{"$ref": f"#/components/messages/Server_{k}"} for k in FRAMES_FROM_SERVER]}},
        }},
        "components": {"messages": messages, "schemas": schemas},
    }
