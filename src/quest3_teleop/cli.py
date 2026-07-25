"""Diagnostic command for the robot-independent Quest source."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import signal
import sys
import time

from .session import Quest3UsbSession
from .usb import QuestUsbError


class _ShutdownRequested(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = int(signum)
        super().__init__(signal.Signals(signum).name)


def _shutdown_handler(signum: int, _frame: object) -> None:
    raise _ShutdownRequested(signum)


_SHUTDOWN_SIGNALS = tuple(
    signum
    for signum in (
        signal.SIGINT,
        signal.SIGTERM,
        getattr(signal, "SIGHUP", None),
    )
    if signum is not None
)


@contextmanager
def _signal_handlers(
    handler: signal.Handlers,
    signals: tuple[int, ...],
) -> Iterator[None]:
    previous = {signum: signal.getsignal(signum) for signum in signals}
    for signum in signals:
        signal.signal(signum, handler)
    try:
        yield
    finally:
        for signum, old_handler in previous.items():
            signal.signal(signum, old_handler)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quest3-teleop")
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser(
        "probe",
        help="start the USB tracker and validate Quest hand frames",
    )
    probe.add_argument(
        "--port",
        type=int,
        default=8765,
        help="desktop localhost port mirrored to Quest localhost (default: 8765)",
    )
    probe.add_argument(
        "--seconds",
        type=float,
        default=30.0,
        help="probe duration after tracking starts (default: 30)",
    )
    probe.add_argument(
        "--adb",
        default=None,
        help="optional path to adb; otherwise use QUEST3_ADB or automatic discovery",
    )
    probe.add_argument(
        "--serial",
        default=None,
        help="ADB serial when more than one Android/Quest device is attached",
    )
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in [1, 65535]")
    if args.seconds <= 0.0:
        parser.error("--seconds must be positive")

    source: Quest3UsbSession | None = None
    external_signals = tuple(
        signum for signum in _SHUTDOWN_SIGNALS if signum != signal.SIGINT
    )
    with _signal_handlers(_shutdown_handler, external_signals):
        try:
            source = Quest3UsbSession(
                port=args.port,
                adb=args.adb,
                serial=args.serial,
            ).start()
            print(
                f"Quest tracking started through USB device {source.serial} on "
                f"localhost:{source.port} "
                f"(initial_frames={source.initial_frames}).",
                flush=True,
            )
            deadline = time.monotonic() + args.seconds
            last_report = 0.0
            while time.monotonic() < deadline:
                source.poll()
                now = time.monotonic()
                if now - last_report >= 1.0:
                    frame = source.latest
                    print(
                        f"accepted={source.stats.accepted} "
                        f"partial={source.stats.partial} "
                        f"malformed={source.stats.malformed} "
                        f"both={bool(frame and frame.both_hands_tracked)} "
                        f"age_ms={source.age_s() * 1000.0:.1f}",
                        flush=True,
                    )
                    last_report = now
                time.sleep(0.01)
            if source.stats.accepted == 0:
                print("FAIL: no Quest hand frames arrived.", flush=True)
                return 2
            print("PASS: Quest hand frames received over USB.", flush=True)
            return 0
        except KeyboardInterrupt:
            print("\nStopping Quest tracker...", flush=True)
            return 130
        except _ShutdownRequested as exc:
            name = signal.Signals(exc.signum).name
            print(f"\nStopping Quest tracker after {name}...", flush=True)
            return 128 + exc.signum
        except (QuestUsbError, RuntimeError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr, flush=True)
            return 2
        finally:
            if source is not None:
                with _signal_handlers(signal.SIG_IGN, _SHUTDOWN_SIGNALS):
                    source.close()


if __name__ == "__main__":
    raise SystemExit(main())
