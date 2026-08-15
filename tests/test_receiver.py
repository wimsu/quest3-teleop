from __future__ import annotations

import asyncio
import json
import socket

from aiohttp import ClientSession

from quest3_teleop.model import JOINT_NAMES
from quest3_teleop.receiver import Quest3Receiver


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def sample_packet() -> dict[str, object]:
    positions, rotations = [], []
    for index in range(len(JOINT_NAMES)):
        positions.extend((0.1 + index * 0.001, 1.0 + index * 0.001, -0.2))
        rotations.extend((0.0, 0.0, 0.0, 1.0))
    hand = {
        "tracked": True,
        "positions_m": positions,
        "rotations_xyzw": rotations,
        "radii_m": [0.008] * 25,
    }
    return {
        "type": "quest3.hand_frame.v1",
        "sequence": 0,
        "sender_timestamp_ns": 10,
        "reference_space": "local",
        "head": None,
        "hands": {"left": hand, "right": hand},
    }


def test_receiver_serves_page_and_accepts_latest_frame() -> None:
    port = free_port()
    receiver = Quest3Receiver(port=port)

    async def exchange() -> None:
        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/") as response:
                html = await response.text()
                assert response.headers["Cache-Control"] == "no-store"
                assert "__QUEST3_ASSET_VERSION__" not in html
                assert "/app.js?v=" in html
            async with session.get(f"http://127.0.0.1:{port}/app.js") as response:
                assert response.headers["Cache-Control"] == "no-store"
                script = await response.text()
                assert "quest3StartTracking" in script
                assert '"immersive-vr"' in script
                assert '"immersive-ar"' not in script
                assert "framebufferScaleFactor: 0.25" in script
                assert "lastSentMs" not in script
            async with session.get(f"http://127.0.0.1:{port}/health") as response:
                health = await response.json()
                assert health["transport"] == "usb"
                assert health["accepted"] == 0
            async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                await ws.send_str(json.dumps(sample_packet()))
                for _ in range(100):
                    if receiver.stats.accepted:
                        return
                    await asyncio.sleep(0.01)
                raise AssertionError("receiver did not accept test frame")

    try:
        asyncio.run(exchange())
        frame = receiver.poll()
        assert frame is not None and frame.both_hands_tracked
        assert receiver.poll() is None
        assert receiver.age_s() < 1.0
    finally:
        receiver.close()
