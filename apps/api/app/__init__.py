"""FastAPI orchestrator process.

Thin transport layer: HTTP/WebSocket in, :mod:`orchestrator` calls out. Business
logic does not live here — if a router grows a decision, it belongs in the
orchestrator package instead, where it can be tested without a server.
"""
