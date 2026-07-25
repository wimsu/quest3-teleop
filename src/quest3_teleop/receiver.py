"""Local HTTP/WebSocket receiver serving the Quest WebXR client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import socket
import threading
import time
from typing import Any

from aiohttp import WSMsgType, web

from .model import HandFrame
from .protocol import HandPacketError, parse_hand_frame


@dataclass
class ReceiverStats:
    accepted: int = 0
    malformed: int = 0
    partial: int = 0
    connections: int = 0


class Quest3Receiver:
    """Latest-only Quest hand source bound only to desktop localhost."""

    def __init__(self, *, port: int = 8765, start: bool = True) -> None:
        self.host = "127.0.0.1"
        self.port = int(port)
        self.stats = ReceiverStats()
        self._lock = threading.Lock()
        self._latest: HandFrame | None = None
        self._last_polled = 0
        self._accepted_sequence = 0
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._startup_error: BaseException | None = None
        if start:
            self.start()

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/"

    @property
    def description(self) -> str:
        return f"Quest Browser WebXR over USB adb reverse → localhost:{self.port}"

    @property
    def latest(self) -> HandFrame | None:
        with self._lock:
            return self._latest

    def start(self, timeout_s: float = 5.0) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_server, name="quest3-webxr", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout_s):
            raise RuntimeError(f"Quest WebXR server did not start on {self.host}:{self.port}")
        if self._startup_error is not None:
            raise RuntimeError(
                f"Quest WebXR server failed: {self._startup_error}"
            ) from self._startup_error

    def _run_server(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/app.js", self._javascript)
        app.router.add_get("/health", self._health)
        app.router.add_get("/ws", self._websocket)
        runner = web.AppRunner(app, access_log=None)
        self._runner = runner
        try:
            loop.run_until_complete(runner.setup())
            loop.run_until_complete(web.TCPSite(runner, self.host, self.port).start())
            self._ready.set()
            loop.run_forever()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            try:
                loop.run_until_complete(runner.cleanup())
            finally:
                loop.close()

    @staticmethod
    def _web_path(filename: str) -> Path:
        return Path(__file__).resolve().parent / "web" / filename

    async def _index(self, _request: web.Request) -> web.Response:
        javascript = self._web_path("app.js")
        html = self._web_path("index.html").read_text().replace(
            "__QUEST3_ASSET_VERSION__",
            str(javascript.stat().st_mtime_ns),
        )
        return web.Response(
            text=html,
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def _javascript(self, _request: web.Request) -> web.FileResponse:
        return web.FileResponse(
            self._web_path("app.js"),
            headers={
                "Content-Type": "text/javascript; charset=utf-8",
                "Cache-Control": "no-store",
            },
        )

    async def _health(self, _request: web.Request) -> web.Response:
        with self._lock:
            accepted = self.stats.accepted
            connections = self.stats.connections
        return web.json_response(
            {
                "service": "quest3-teleop",
                "protocol": 1,
                "transport": "usb",
                "accepted": accepted,
                "connections": connections,
            }
        )

    async def _websocket(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=10.0, max_msg_size=256 * 1024)
        await ws.prepare(request)
        with self._lock:
            self.stats.connections += 1
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    with self._lock:
                        self.stats.malformed += 1
                    continue
                try:
                    value = json.loads(message.data)
                    with self._lock:
                        next_sequence = self._accepted_sequence + 1
                    frame = parse_hand_frame(
                        value,
                        sequence=next_sequence,
                        received_monotonic_ns=time.monotonic_ns(),
                    )
                except (json.JSONDecodeError, HandPacketError):
                    with self._lock:
                        self.stats.malformed += 1
                    continue
                with self._lock:
                    self._accepted_sequence = next_sequence
                    self._latest = frame
                    self.stats.accepted += 1
                    if not frame.both_hands_tracked:
                        self.stats.partial += 1
        finally:
            with self._lock:
                self.stats.connections = max(0, self.stats.connections - 1)
        return ws

    def poll(self) -> HandFrame | None:
        with self._lock:
            if self._latest is None or self._latest.sequence == self._last_polled:
                return None
            self._last_polled = self._latest.sequence
            return self._latest

    def age_s(self, now_ns: int | None = None) -> float:
        frame = self.latest
        if frame is None:
            return float("inf")
        now_ns = time.monotonic_ns() if now_ns is None else int(now_ns)
        return max(0.0, (now_ns - frame.received_monotonic_ns) / 1e9)

    def close(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=3.0)
        self._thread = None

    def __enter__(self) -> "Quest3Receiver":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["Quest3Receiver", "ReceiverStats"]
