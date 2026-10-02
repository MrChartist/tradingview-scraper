# Extending Tickvale

Tickvale is built so that new capabilities, or whole other products, can be merged in later without touching the core. Everything is one of two things:

* an **operation**: ask a question, get one answer (`quotes`, `candles`, `screener`...). Works on the WebSocket (`{"op": "..."}`) and, for the built-ins, on REST.
* a **channel**: a live feed you subscribe to (`quotes`, `movers`).

Both show up automatically in the `hello` message, in `GET /v1/operations`, and share the same API-key checks, rate limits, error format and validation.

## Add an operation (about ten lines)

```python
# my_company/ops.py
from pydantic import Field
from api.operations import Params, Result, operation

class WatchlistParams(Params):
    user_id: str = Field(..., max_length=40)

@operation("nesto.watchlist", "A user's saved symbols.", WatchlistParams)
def watchlist(p, ctx):
    symbols = load_symbols_from_my_database(p.user_id)      # any blocking code is fine
    return Result(symbols, {"count": len(symbols)})
```

* The function is plain blocking code; Tickvale runs it in a worker thread.
* `Result(data, meta)` becomes `{"type": "result", "data": ..., "meta": ...}`.
* Raise `ApiError(status, message, hint="what to do")` for problems the caller can fix.
* The params model gives you validation for free; unknown parameters are rejected.
* Use a prefix (`nesto.`) so names never collide. A duplicate name raises at startup.
* To fetch market data inside your operation, call the same functions the built-ins use, for example `from api import services as svc` then `svc.snapshot_quotes([...])`, or `execute("quotes", {...}, ctx)`.

## Add a live channel

```python
# my_company/channels.py
import asyncio
from api.socket import Channel, channel

@channel
class AlertsChannel(Channel):
    name = "nesto.alerts"
    summary = "Price alerts for the user."
    params = "user_id"

    async def subscribe(self, session, params):
        task = asyncio.create_task(self._watch(session, params["user_id"]))
        session.state.setdefault("alerts", []).append(task)
        return {"channel": self.name}

    async def unsubscribe(self, session, params):
        for task in session.state.pop("alerts", []):
            task.cancel()
        return {"channel": self.name}

    async def close(self, session):                 # always called when the connection ends
        await self.unsubscribe(session, {})

    async def _watch(self, session, user_id):
        while True:
            alert = await next_alert_for(user_id)
            await session.send({"type": "update", "channel": self.name, "data": alert})
```

`session.send(frame)` writes to this client; `session.ctx`, `session.hub` (the shared live quote hub) and `session.settings` are available. Clean up in `close`, which always runs.

## Load it

Put the modules on the Python path and name them in the environment:

```bash
PLUGINS=my_company.ops,my_company.channels
```

Importing a module is what registers its operations and channels. A plugin that fails to import stops the server on startup instead of failing quietly.

## Using a different data source

Data sources are providers (see [PROVIDERS.md](PROVIDERS.md)): a plugin registers one, and `PROVIDERS` decides the order. Clients written against the SDKs or the socket do not change.

## Checklist for a new capability

1. Name it with your prefix; write the params model with limits (`max_length`, `ge`, `le`).
2. Return plain JSON-friendly data and put counts and notes in `meta`.
3. Give every error a `hint`.
4. Test it over the socket the way `tests/test_socket.py` does (see `test_plugin_module_adds_an_operation_and_a_channel`).
