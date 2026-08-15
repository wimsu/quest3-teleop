from __future__ import annotations

import asyncio
import json
import socket

from aiohttp import web

import quest3_teleop.browser as browser_module
from quest3_teleop.browser import QuestBrowserAutomation


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_start_page_uses_user_gesture_and_waits_for_frames() -> None:
    async def scenario() -> None:
        port = free_port()
        page_url = f"http://127.0.0.1:{port}/teleop/"
        state = {"accepted": 0, "gesture": False, "wake_reassertions": 0}

        async def targets(_request: web.Request) -> web.Response:
            return web.json_response(
                [
                    {
                        "id": "fresh",
                        "type": "page",
                        "url": page_url,
                        "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/fresh",
                    }
                ]
            )

        async def page_socket(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            async for message in websocket:
                value = json.loads(message.data)
                expression = value["params"]["expression"]
                if value["params"].get("userGesture") is True:
                    state["gesture"] = value["params"].get("userGesture") is True
                    result = json.dumps(
                        {"activated": True, "phase": "requesting_session"}
                    )
                else:
                    result = json.dumps(
                        {
                            "ready": True,
                            "status": "Desktop connected.",
                            "phase": "ready",
                        }
                    )
                await websocket.send_json(
                    {
                        "id": value["id"],
                        "result": {"result": {"type": "string", "value": result}},
                    }
                )
            return websocket

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"accepted": state["accepted"]})

        app = web.Application()
        app.router.add_get("/json/list", targets)
        app.router.add_get("/devtools/page/fresh", page_socket)
        app.router.add_get("/teleop/health", health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            automation = QuestBrowserAutomation(debug_port=port)

            def reassert_wake() -> None:
                state["wake_reassertions"] += 1
                if state["wake_reassertions"] >= 2:
                    state["accepted"] = 1

            result = await automation._start_page(  # noqa: SLF001 - protocol test
                page_url,
                after_session_request=reassert_wake,
                timeout_s=2.0,
            )
            assert result.accepted_frames == 1
            assert state["gesture"] is True
            assert state["wake_reassertions"] >= 2
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_start_page_brings_ready_hidden_page_to_front() -> None:
    async def scenario() -> None:
        port = free_port()
        page_url = f"http://127.0.0.1:{port}/teleop/"
        state = {"accepted": 0, "visible": False, "brought_to_front": 0}

        async def targets(_request: web.Request) -> web.Response:
            return web.json_response(
                [
                    {
                        "id": "hidden",
                        "type": "page",
                        "url": page_url,
                        "webSocketDebuggerUrl": (
                            f"ws://127.0.0.1:{port}/devtools/page/hidden"
                        ),
                    }
                ]
            )

        async def page_socket(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            async for message in websocket:
                value = json.loads(message.data)
                if value["method"] == "Page.bringToFront":
                    state["visible"] = True
                    state["brought_to_front"] += 1
                    result: dict[str, object] = {}
                elif value["params"].get("userGesture") is True:
                    state["accepted"] = 1
                    result = {
                        "result": {
                            "type": "string",
                            "value": json.dumps(
                                {"activated": True, "phase": "requesting_session"}
                            ),
                        }
                    }
                else:
                    result = {
                        "result": {
                            "type": "string",
                            "value": json.dumps(
                                {
                                    "ready": True,
                                    "status": "Ready.",
                                    "phase": "ready",
                                    "visible": state["visible"],
                                }
                            ),
                        }
                    }
                await websocket.send_json({"id": value["id"], "result": result})
            return websocket

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"accepted": state["accepted"]})

        app = web.Application()
        app.router.add_get("/json/list", targets)
        app.router.add_get("/devtools/page/hidden", page_socket)
        app.router.add_get("/teleop/health", health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            automation = QuestBrowserAutomation(debug_port=port)
            result = await automation._start_page(  # noqa: SLF001
                page_url,
                after_session_request=None,
                timeout_s=2.0,
            )
            assert result.accepted_frames == 1
            assert state["brought_to_front"] == 1
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_navigate_page_replaces_cold_start_new_tab() -> None:
    async def scenario() -> None:
        port = free_port()
        navigated: list[str] = []

        async def targets(_request: web.Request) -> web.Response:
            return web.json_response(
                [
                    {
                        "id": "new-tab",
                        "type": "page",
                        "url": "chrome://panel-app-nav/ntp",
                        "webSocketDebuggerUrl": (
                            f"ws://127.0.0.1:{port}/devtools/page/new-tab"
                        ),
                    }
                ]
            )

        async def page_socket(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            async for message in websocket:
                value = json.loads(message.data)
                assert value["method"] == "Page.navigate"
                navigated.append(value["params"]["url"])
                await websocket.send_json(
                    {
                        "id": value["id"],
                        "result": {"frameId": "frame"},
                    }
                )
            return websocket

        app = web.Application()
        app.router.add_get("/json/list", targets)
        app.router.add_get("/devtools/page/new-tab", page_socket)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            automation = QuestBrowserAutomation(debug_port=port)
            await automation._navigate_page(  # noqa: SLF001
                "http://localhost:8765/?_quest3_launch=1",
                timeout_s=1.0,
            )
            assert navigated == ["http://localhost:8765/?_quest3_launch=1"]
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_close_matching_pages_targets_only_exact_teleop_url() -> None:
    async def scenario() -> None:
        port = free_port()
        page_url = f"http://127.0.0.1:{port}/teleop/"
        closed: list[str] = []
        ended: list[str] = []

        async def targets(_request: web.Request) -> web.Response:
            return web.json_response(
                [
                    {
                        "id": "teleop",
                        "type": "page",
                        "url": page_url,
                        "webSocketDebuggerUrl": (
                            f"ws://127.0.0.1:{port}/devtools/page/teleop"
                        ),
                    },
                    {"id": "other", "type": "page", "url": "https://example.com/"},
                ]
            )

        async def version(_request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "webSocketDebuggerUrl": (
                        f"ws://127.0.0.1:{port}/devtools/browser"
                    )
                }
            )

        async def browser_socket(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            async for message in websocket:
                value = json.loads(message.data)
                closed.append(value["params"]["targetId"])
                await websocket.send_json(
                    {
                        "id": value["id"],
                        "result": {"success": True},
                    }
                )
            return websocket

        async def page_socket(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            async for message in websocket:
                value = json.loads(message.data)
                ended.append(value["params"]["expression"])
                await websocket.send_json(
                    {
                        "id": value["id"],
                        "result": {
                            "result": {"type": "string", "value": "ended"}
                        },
                    }
                )
            return websocket

        app = web.Application()
        app.router.add_get("/json/list", targets)
        app.router.add_get("/json/version", version)
        app.router.add_get("/devtools/browser", browser_socket)
        app.router.add_get("/devtools/page/teleop", page_socket)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            automation = QuestBrowserAutomation(debug_port=port)
            matching = await automation._close_matching_pages(  # noqa: SLF001
                page_url,
                timeout_s=2.0,
            )
            assert matching == {"teleop"}
            assert closed == ["teleop"]
            assert any("quest3StopTracking" in expression for expression in ended)
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_active_tracking_without_visible_hands_does_not_abort_startup(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        port = free_port()
        page_url = f"http://127.0.0.1:{port}/teleop/"

        async def targets(_request: web.Request) -> web.Response:
            return web.json_response(
                [
                    {
                        "id": "fresh",
                        "type": "page",
                        "url": page_url,
                        "webSocketDebuggerUrl": (
                            f"ws://127.0.0.1:{port}/devtools/page/fresh"
                        ),
                    }
                ]
            )

        async def page_socket(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            async for message in websocket:
                value = json.loads(message.data)
                if value["params"].get("userGesture") is True:
                    result = {"activated": True, "phase": "requesting_session"}
                elif "ready:" in value["params"]["expression"]:
                    result = {
                        "ready": True,
                        "status": "Ready.",
                        "phase": "ready",
                    }
                else:
                    result = {
                        "phase": "tracking",
                        "status": "Tracking; hands outside view.",
                        "error": "",
                    }
                await websocket.send_json(
                    {
                        "id": value["id"],
                        "result": {
                            "result": {
                                "type": "string",
                                "value": json.dumps(result),
                            }
                        },
                    }
                )
            return websocket

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"accepted": 0})

        app = web.Application()
        app.router.add_get("/json/list", targets)
        app.router.add_get("/devtools/page/fresh", page_socket)
        app.router.add_get("/teleop/health", health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            monkeypatch.setattr(
                browser_module,
                "TRACKING_FRAME_GRACE_S",
                0.05,
            )
            automation = QuestBrowserAutomation(debug_port=port)
            result = await automation._start_page(  # noqa: SLF001
                page_url,
                after_session_request=None,
                timeout_s=1.0,
            )
            assert result.accepted_frames == 0
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
