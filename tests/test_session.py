from __future__ import annotations

from types import SimpleNamespace

import pytest

import quest3_teleop.session as session_module
from quest3_teleop.session import Quest3UsbSession


def test_session_owns_receiver_bridge_and_shutdown_order(monkeypatch) -> None:
    events: list[object] = []
    expected_frame = object()

    class FakeReceiver:
        def __init__(self, *, port: int, start: bool) -> None:
            assert not start
            self.port = port
            self.url = f"http://localhost:{port}/"
            self.stats = SimpleNamespace(accepted=0)
            self.latest = expected_frame

        def start(self) -> None:
            events.append("receiver_start")

        def poll(self) -> object:
            return expected_frame

        def age_s(self, _now_ns: int | None = None) -> float:
            return 0.01

        def close(self) -> None:
            events.append("receiver_close")

    class FakeBridge:
        def __init__(self, *, port: int, adb: object, serial: str | None) -> None:
            self.port = port
            self.serial = serial

        def configure(self) -> str:
            self.serial = "quest"
            events.append("bridge_configure")
            return self.serial

        def start_tracking(self, url: str) -> object:
            events.append(("bridge_start", url))
            return SimpleNamespace(accepted_frames=12)

        def close(self) -> None:
            events.append("bridge_close")

    monkeypatch.setattr(session_module, "Quest3Receiver", FakeReceiver)
    monkeypatch.setattr(session_module, "QuestUsbBridge", FakeBridge)

    with Quest3UsbSession(port=9123) as source:
        assert source.port == 9123
        assert source.serial == "quest"
        assert source.poll() is expected_frame
        assert source.latest is expected_frame
        assert source.initial_frames == 12

    assert events == [
        "receiver_start",
        "bridge_configure",
        ("bridge_start", "http://localhost:9123/"),
        "bridge_close",
        "receiver_close",
    ]


def test_failed_start_still_closes_receiver_and_bridge(monkeypatch) -> None:
    events: list[str] = []

    class FakeReceiver:
        def __init__(self, **_kwargs: object) -> None:
            self.port = 8765
            self.url = "http://localhost:8765/"
            self.stats = object()
            self.latest = None

        def start(self) -> None:
            events.append("receiver_start")

        def close(self) -> None:
            events.append("receiver_close")

    class FakeBridge:
        def __init__(self, **_kwargs: object) -> None:
            self.serial = None

        def configure(self) -> str:
            events.append("bridge_configure")
            raise RuntimeError("no Quest")

        def close(self) -> None:
            events.append("bridge_close")

    monkeypatch.setattr(session_module, "Quest3Receiver", FakeReceiver)
    monkeypatch.setattr(session_module, "QuestUsbBridge", FakeBridge)

    with pytest.raises(RuntimeError, match="no Quest"):
        Quest3UsbSession().start()

    assert events == [
        "receiver_start",
        "bridge_configure",
        "bridge_close",
        "receiver_close",
    ]
