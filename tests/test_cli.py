from __future__ import annotations

from types import SimpleNamespace
import sys

import quest3_teleop.cli as cli


def test_probe_uses_port_adb_serial_and_always_closes(monkeypatch) -> None:
    events: list[object] = []

    class FakeSession:
        def __init__(
            self,
            *,
            port: int,
            adb: str | None,
            serial: str | None,
        ) -> None:
            events.append(("init", port, adb, serial))
            self.port = port
            self.serial = serial
            self.initial_frames = 4
            self.stats = SimpleNamespace(accepted=1, partial=0, malformed=0)
            self.latest = SimpleNamespace(both_hands_tracked=True)

        def start(self) -> "FakeSession":
            events.append("start")
            return self

        def poll(self) -> None:
            events.append("poll")

        def age_s(self) -> float:
            return 0.01

        def close(self) -> None:
            events.append("close")

    monotonic_values = iter((10.0, 10.0, 10.1, 10.2))
    monkeypatch.setattr(cli, "Quest3UsbSession", FakeSession)
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "quest3-teleop",
            "probe",
            "--port",
            "9876",
            "--seconds",
            "0.05",
            "--adb",
            "/opt/adb",
            "--serial",
            "quest",
        ],
    )

    assert cli.main() == 0
    assert events[0] == ("init", 9876, "/opt/adb", "quest")
    assert "start" in events
    assert events[-1] == "close"
