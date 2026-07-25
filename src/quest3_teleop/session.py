"""One-owner USB tracking session for robot applications."""

from __future__ import annotations

from pathlib import Path

from .model import HandFrame
from .receiver import Quest3Receiver, ReceiverStats
from .usb import QuestUsbBridge


class Quest3UsbSession:
    """Own the receiver, ADB bridge, Quest Browser, and power lifecycle.

    Robot projects consume :class:`HandFrame` and keep all retargeting and
    hardware behavior outside this class.
    """

    def __init__(
        self,
        *,
        port: int = 8765,
        adb: str | Path | None = None,
        serial: str | None = None,
    ) -> None:
        if not 1 <= int(port) <= 65535:
            raise ValueError("port must be in [1, 65535]")
        self.receiver = Quest3Receiver(port=int(port), start=False)
        self.bridge = QuestUsbBridge(
            port=int(port),
            adb=adb,
            serial=serial,
        )
        self.initial_frames = 0
        self._started = False
        self._closed = False

    @property
    def port(self) -> int:
        return self.receiver.port

    @property
    def serial(self) -> str | None:
        return self.bridge.serial

    @property
    def stats(self) -> ReceiverStats:
        return self.receiver.stats

    @property
    def latest(self) -> HandFrame | None:
        return self.receiver.latest

    def start(self) -> "Quest3UsbSession":
        if self._closed:
            raise RuntimeError("a closed Quest3UsbSession cannot be restarted")
        if self._started:
            return self
        self.receiver.start()
        try:
            self.bridge.configure()
            self.initial_frames = self.bridge.start_tracking(
                self.receiver.url
            ).accepted_frames
        except BaseException:
            self.close()
            raise
        self._started = True
        return self

    def poll(self) -> HandFrame | None:
        return self.receiver.poll()

    def age_s(self, now_ns: int | None = None) -> float:
        return self.receiver.age_s(now_ns)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.bridge.close()
        finally:
            self.receiver.close()
            self._started = False
            self._closed = True

    def __enter__(self) -> "Quest3UsbSession":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["Quest3UsbSession"]
