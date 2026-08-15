"""Quest Browser automation over its authorized USB debugging channel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp


TRACKING_FRAME_GRACE_S = 3.0


class QuestBrowserError(RuntimeError):
    """Raised when Quest Browser cannot enter the teleop WebXR session."""


@dataclass(frozen=True)
class BrowserStartResult:
    accepted_frames: int


class QuestBrowserAutomation:
    """Small synchronous facade around Quest Browser's Chrome DevTools API."""

    def __init__(self, *, debug_port: int) -> None:
        self.debug_port = int(debug_port)
        self.http_origin = f"http://127.0.0.1:{self.debug_port}"

    @staticmethod
    def _run(coroutine: Any) -> Any:
        running_loop = False
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            running_loop = True
        if running_loop:
            coroutine.close()
            raise QuestBrowserError(
                "browser automation must be called from synchronous operator code"
            )
        return asyncio.run(coroutine)

    def close_matching_pages(self, url: str, *, timeout_s: float = 3.0) -> set[str]:
        """Close only stale tabs belonging to this exact local teleop URL."""

        return self._run(self._close_matching_pages(url, timeout_s=timeout_s))

    def start_page(
        self,
        url: str,
        *,
        after_session_request: Callable[[], None] | None = None,
        timeout_s: float = 20.0,
    ) -> BrowserStartResult:
        """Activate the page's existing Start button with a trusted gesture."""

        return self._run(
            self._start_page(
                url,
                after_session_request=after_session_request,
                timeout_s=timeout_s,
            )
        )

    def navigate_page(self, url: str, *, timeout_s: float = 5.0) -> None:
        """Navigate Quest Browser's live page after a cold process start."""

        return self._run(self._navigate_page(url, timeout_s=timeout_s))

    def _local_websocket_url(self, value: str) -> str:
        parts = urlsplit(value)
        return urlunsplit(
            (
                parts.scheme,
                f"127.0.0.1:{self.debug_port}",
                parts.path,
                parts.query,
                parts.fragment,
            )
        )

    @staticmethod
    def _same_page(actual: object, expected: str) -> bool:
        if not isinstance(actual, str):
            return False
        actual_parts = urlsplit(actual)
        expected_parts = urlsplit(expected)
        actual_query = urlencode([
            (key, value)
            for key, value in parse_qsl(actual_parts.query, keep_blank_values=True)
            if key != "_quest3_launch"
        ])
        expected_query = urlencode([
            (key, value)
            for key, value in parse_qsl(expected_parts.query, keep_blank_values=True)
            if key != "_quest3_launch"
        ])
        return (
            actual_parts.scheme == expected_parts.scheme
            and actual_parts.netloc == expected_parts.netloc
            and (actual_parts.path.rstrip("/") or "/")
            == (expected_parts.path.rstrip("/") or "/")
            and actual_query == expected_query
        )

    async def _json(
        self,
        session: aiohttp.ClientSession,
        path: str,
    ) -> Any:
        async with session.get(f"{self.http_origin}{path}") as response:
            response.raise_for_status()
            return await response.json()

    async def _targets(self, session: aiohttp.ClientSession) -> list[dict[str, Any]]:
        value = await self._json(session, "/json/list")
        if not isinstance(value, list):
            raise QuestBrowserError("Quest Browser returned an invalid target list")
        return [target for target in value if isinstance(target, dict)]

    async def _navigate_page(self, url: str, *, timeout_s: float) -> str:
        deadline = time.monotonic() + max(0.5, timeout_s)
        timeout = aiohttp.ClientTimeout(total=None, connect=1.0, sock_read=2.0)
        last_error = "Quest Browser has no debuggable page"
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while time.monotonic() < deadline:
                    try:
                        targets = await self._targets(session)
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        last_error = str(exc) or last_error
                        await asyncio.sleep(0.1)
                        continue
                    pages = [
                        target
                        for target in targets
                        if target.get("type") == "page"
                        and isinstance(target.get("webSocketDebuggerUrl"), str)
                    ]
                    if not pages:
                        await asyncio.sleep(0.1)
                        continue
                    # Browser is force-stopped before launch, so this is its
                    # single new-tab page. Android's VIEW intent is not trusted:
                    # recent Quest releases cold-start on their internal NTP.
                    target = pages[-1]
                    websocket_url = str(target["webSocketDebuggerUrl"])
                    async with session.ws_connect(
                        self._local_websocket_url(websocket_url)
                    ) as websocket:
                        await self._command(
                            websocket,
                            command_id=71,
                            method="Page.navigate",
                            params={"url": url},
                            timeout_s=max(0.5, timeout_s),
                        )
                    return
                raise QuestBrowserError(
                    f"could not navigate Quest Browser before timeout ({last_error})"
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise QuestBrowserError(
                f"could not navigate Quest Browser: {exc}"
            ) from exc

    async def _browser_websocket_url(self, session: aiohttp.ClientSession) -> str:
        value = await self._json(session, "/json/version")
        websocket_url = value.get("webSocketDebuggerUrl") if isinstance(value, dict) else None
        if not isinstance(websocket_url, str):
            raise QuestBrowserError("Quest Browser did not expose a debugging websocket")
        return self._local_websocket_url(websocket_url)

    @staticmethod
    async def _command(
        websocket: aiohttp.ClientWebSocketResponse,
        *,
        command_id: int,
        method: str,
        params: dict[str, Any],
        timeout_s: float,
    ) -> dict[str, Any]:
        await websocket.send_json(
            {
                "id": command_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise QuestBrowserError(f"Quest Browser did not answer {method}")
            message = await websocket.receive(timeout=remaining)
            if message.type != aiohttp.WSMsgType.TEXT:
                raise QuestBrowserError(f"Quest Browser debugging socket closed during {method}")
            try:
                value = json.loads(message.data)
            except json.JSONDecodeError:
                continue
            if value.get("id") != command_id:
                continue
            if "error" in value:
                raise QuestBrowserError(f"{method} failed: {value['error']}")
            return value

    async def _close_matching_pages(self, url: str, *, timeout_s: float) -> set[str]:
        timeout = aiohttp.ClientTimeout(total=max(0.2, timeout_s))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                targets = await self._targets(session)
                matching = {
                    str(target["id"])
                    for target in targets
                    if target.get("type") == "page"
                    and self._same_page(target.get("url"), url)
                    and "id" in target
                }
                if not matching:
                    return set()
                # Explicitly end an owned immersive session before closing its
                # tab. Closing only the page can leave Quest's WebVRActivity
                # holding hand tracking until Browser is restarted.
                for target in targets:
                    if str(target.get("id")) not in matching:
                        continue
                    try:
                        await self._end_page_session(
                            session,
                            target,
                            timeout_s=max(0.2, timeout_s),
                        )
                    except (
                        aiohttp.ClientError,
                        asyncio.TimeoutError,
                        QuestBrowserError,
                    ):
                        pass
                websocket_url = await self._browser_websocket_url(session)
                async with session.ws_connect(websocket_url) as websocket:
                    for command_id, target_id in enumerate(sorted(matching), start=1):
                        await self._command(
                            websocket,
                            command_id=command_id,
                            method="Target.closeTarget",
                            params={"targetId": target_id},
                            timeout_s=max(0.2, timeout_s),
                        )
                return matching
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # The browser may not be running yet. Opening the URL below starts
            # it, so absence of an initial debugging socket is not an error.
            return set()

    async def _end_page_session(
        self,
        session: aiohttp.ClientSession,
        target: dict[str, Any],
        *,
        timeout_s: float,
    ) -> None:
        websocket_url = target.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            return
        async with session.ws_connect(self._local_websocket_url(websocket_url)) as websocket:
            await self._command(
                websocket,
                command_id=91,
                method="Runtime.evaluate",
                params={
                    "expression": (
                        "(async()=>{"
                        "if(typeof window.quest3StopTracking==='function'){"
                        "await window.quest3StopTracking();"
                        "}"
                        "return window.quest3TeleopState?.phase||'';"
                        "})()"
                    ),
                    "awaitPromise": True,
                    "returnByValue": True,
                },
                timeout_s=timeout_s,
            )

    @staticmethod
    def _runtime_value(response: dict[str, Any]) -> Any:
        try:
            result = response["result"]["result"]
        except (KeyError, TypeError) as exc:
            raise QuestBrowserError(
                "Quest Browser returned an invalid Runtime.evaluate response"
            ) from exc
        if result.get("subtype") == "error":
            raise QuestBrowserError(result.get("description", "Quest Browser JavaScript failed"))
        return result.get("value")

    async def _page_state(
        self,
        session: aiohttp.ClientSession,
        target: dict[str, Any],
    ) -> tuple[bool, str, str, bool]:
        websocket_url = target.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            return False, "debugging websocket is unavailable", "", False
        async with session.ws_connect(self._local_websocket_url(websocket_url)) as websocket:
            response = await self._command(
                websocket,
                command_id=1,
                method="Runtime.evaluate",
                params={
                    "expression": (
                        "JSON.stringify({"
                        "ready:("
                        "!document.querySelector('#start')?.disabled"
                        "&&typeof window.quest3StartTracking==='function'"
                        "&&window.quest3TeleopState?.phase==='ready'"
                        "),"
                        "status:document.querySelector('#status')?.textContent||'',"
                        "phase:window.quest3TeleopState?.phase||'',"
                        "visible:document.visibilityState==='visible',"
                        "clientLoaded:typeof window.quest3StartTracking==='function'"
                        "})"
                    ),
                    "returnByValue": True,
                },
                timeout_s=2.0,
            )
        raw = self._runtime_value(response)
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise QuestBrowserError(
                "Quest teleop page returned an invalid readiness state"
            ) from exc
        return (
            bool(state.get("ready")),
            str(state.get("status", "")),
            str(state.get("phase", "")),
            bool(state.get("visible", True)),
        )

    async def _bring_page_to_front(
        self,
        session: aiohttp.ClientSession,
        target: dict[str, Any],
    ) -> None:
        """Activate a ready teleop tab left hidden by a prior WebVR task."""

        websocket_url = target.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            raise QuestBrowserError("Quest teleop page has no debugging websocket")
        async with session.ws_connect(
            self._local_websocket_url(websocket_url)
        ) as websocket:
            await self._command(
                websocket,
                command_id=4,
                method="Page.bringToFront",
                params={},
                timeout_s=2.0,
            )

    async def _tracking_state(
        self,
        session: aiohttp.ClientSession,
        target: dict[str, Any],
    ) -> tuple[str, str]:
        websocket_url = target.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            return "", "debugging websocket is unavailable"
        async with session.ws_connect(self._local_websocket_url(websocket_url)) as websocket:
            response = await self._command(
                websocket,
                command_id=3,
                method="Runtime.evaluate",
                params={
                    "expression": (
                        "JSON.stringify({"
                        "phase:window.quest3TeleopState?.phase||'',"
                        "status:document.querySelector('#status')?.textContent||'',"
                        "error:window.quest3TeleopState?.error||''"
                        "})"
                    ),
                    "returnByValue": True,
                },
                timeout_s=2.0,
            )
        raw = self._runtime_value(response)
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise QuestBrowserError("Quest teleop page returned invalid tracking state") from exc
        status = str(state.get("status", ""))
        error = str(state.get("error", ""))
        return str(state.get("phase", "")), error or status

    async def _click_start(
        self,
        session: aiohttp.ClientSession,
        target: dict[str, Any],
    ) -> None:
        websocket_url = target.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            raise QuestBrowserError("Quest teleop page has no debugging websocket")
        async with session.ws_connect(self._local_websocket_url(websocket_url)) as websocket:
            response = await self._command(
                websocket,
                command_id=2,
                method="Runtime.evaluate",
                params={
                    "expression": (
                        "JSON.stringify((()=>{"
                        "const activated=navigator.userActivation.isActive;"
                        "if(typeof window.quest3StartTracking!=='function'){"
                        "return {activated,phase:'missing_start_function'};"
                        "}"
                        "void window.quest3StartTracking();"
                        "return {"
                        "activated,"
                        "phase:window.quest3TeleopState?.phase||''"
                        "};"
                        "})())"
                    ),
                    "userGesture": True,
                    "returnByValue": True,
                },
                timeout_s=3.0,
            )
        raw = self._runtime_value(response)
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise QuestBrowserError("Quest Browser returned invalid start state") from exc
        if result.get("activated") is not True:
            raise QuestBrowserError("Quest Browser did not grant transient activation")
        phase = str(result.get("phase", ""))
        if phase not in {"requesting_session", "preparing_session", "tracking"}:
            raise QuestBrowserError(
                f"Quest teleop start function did not run (page phase: {phase or 'unknown'})"
            )

    @staticmethod
    async def _accepted_frames(
        session: aiohttp.ClientSession,
        health_url: str,
    ) -> int:
        async with session.get(health_url) as response:
            response.raise_for_status()
            value = await response.json()
        accepted = value.get("accepted") if isinstance(value, dict) else None
        if not isinstance(accepted, int):
            raise QuestBrowserError("Quest teleop health endpoint omitted accepted frame count")
        return accepted

    async def _start_page(
        self,
        url: str,
        *,
        after_session_request: Callable[[], None] | None,
        timeout_s: float,
    ) -> BrowserStartResult:
        deadline = time.monotonic() + max(1.0, timeout_s)
        timeout = aiohttp.ClientTimeout(total=None, connect=2.0, sock_read=3.0)
        health_url = urljoin(url, "health")
        last_status = "waiting for Quest teleop page"
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                baseline = await self._accepted_frames(session, health_url)
                target: dict[str, Any] | None = None
                while time.monotonic() < deadline:
                    try:
                        targets = await self._targets(session)
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        await asyncio.sleep(0.1)
                        continue
                    candidates = [
                        item
                        for item in targets
                        if item.get("type") == "page"
                        and self._same_page(item.get("url"), url)
                    ]
                    for candidate in candidates:
                        try:
                            ready, status, phase, visible = await self._page_state(
                                session, candidate
                            )
                        except (aiohttp.ClientError, asyncio.TimeoutError, QuestBrowserError):
                            continue
                        last_status = status or last_status
                        if phase == "error" or status.startswith(
                            ("Could not start:", "WebXR unavailable", "This browser")
                        ):
                            raise QuestBrowserError(status)
                        if ready and visible:
                            target = candidate
                            break
                        if ready:
                            last_status = "bringing hidden Quest Browser page to front"
                            try:
                                await self._bring_page_to_front(session, candidate)
                            except (
                                aiohttp.ClientError,
                                asyncio.TimeoutError,
                                QuestBrowserError,
                            ):
                                pass
                    if target is not None:
                        break
                    await asyncio.sleep(0.1)
                if target is None:
                    raise QuestBrowserError(
                        f"Quest teleop page was not ready before timeout ({last_status})"
                    )

                # Quest Browser can report a ready DOM slightly before the
                # WebVR panel becomes foreground-visible.  Calling
                # requestSession() in that window is rejected by Horizon OS.
                await asyncio.sleep(0.75)
                await self._click_start(session, target)
                # Starting a new Quest WebXR activity can re-apply the runtime's
                # "not worn" suspend state. The USB owner reasserts its
                # temporary proximity/wake policy here, after that activity
                # exists, so neck-mounted tracking begins without a headset
                # interaction.
                if after_session_request is not None:
                    after_session_request()
                last_wake_reassertion = time.monotonic()
                last_page_state_check = 0.0
                tracking_confirmed_at: float | None = None
                while time.monotonic() < deadline:
                    if (
                        after_session_request is not None
                        and time.monotonic() - last_wake_reassertion >= 0.5
                    ):
                        after_session_request()
                        last_wake_reassertion = time.monotonic()
                    try:
                        accepted = await self._accepted_frames(session, health_url)
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        await asyncio.sleep(0.05)
                        continue
                    if accepted > baseline:
                        return BrowserStartResult(
                            accepted_frames=accepted - baseline,
                        )
                    if time.monotonic() - last_page_state_check >= 0.25:
                        try:
                            phase, detail = await self._tracking_state(session, target)
                        except (aiohttp.ClientError, asyncio.TimeoutError, QuestBrowserError):
                            pass
                        else:
                            last_status = f"{phase}: {detail}" if phase else detail
                            if phase == "error":
                                raise QuestBrowserError(detail)
                            if phase == "tracking":
                                tracking_confirmed_at = (
                                    time.monotonic()
                                    if tracking_confirmed_at is None
                                    else tracking_confirmed_at
                                )
                        last_page_state_check = time.monotonic()
                    # Once Quest confirms the immersive tracking session, the
                    # operator process should stay alive even when hands are
                    # temporarily outside the headset cameras. The receiver
                    # will report LOST and recover when frames arrive.
                    if (
                        tracking_confirmed_at is not None
                        and time.monotonic() - tracking_confirmed_at
                        >= TRACKING_FRAME_GRACE_S
                    ):
                        return BrowserStartResult(
                            accepted_frames=max(0, accepted - baseline),
                        )
                    await asyncio.sleep(0.05)
                raise QuestBrowserError(
                    "tracker-only WebXR produced no Quest hand frames before timeout"
                    + (f" ({last_status})" if last_status else "")
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise QuestBrowserError(f"Quest Browser automation failed: {exc}") from exc


__all__ = [
    "BrowserStartResult",
    "QuestBrowserAutomation",
    "QuestBrowserError",
]
