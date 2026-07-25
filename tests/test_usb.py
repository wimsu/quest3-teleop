from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from quest3_teleop.usb import QuestUsbBridge, QuestUsbError


def completed(*arguments: str, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")


def test_prepare_and_close_restore_temporary_quest_power_state(
    monkeypatch,
) -> None:
    bridge = QuestUsbBridge(adb=Path("/bin/true"), serial="quest")
    bridge._configured = True  # noqa: SLF001 - isolate ADB command contract
    bridge._debug_port = 43210  # noqa: SLF001
    calls: list[tuple[str, ...]] = []

    def command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return completed(*arguments)

    monkeypatch.setattr(bridge, "_command", command)
    bridge._prepare_unworn_tracking()  # noqa: SLF001
    bridge._reassert_unworn_tracking()  # noqa: SLF001
    bridge.close()

    assert (
        "shell",
        "am",
        "broadcast",
        "-a",
        "com.oculus.vrpowermanager.prox_close",
    ) in calls
    assert ("shell", "svc", "power", "stayon", "usb") in calls
    assert ("shell", "input", "keyevent", "KEYCODE_WAKEUP") in calls
    assert calls.count(
        (
            "shell",
            "am",
            "broadcast",
            "-a",
            "com.oculus.vrpowermanager.automation_disable",
        )
    ) == 2
    assert ("shell", "svc", "power", "stayon", "false") in calls
    assert (
        "shell",
        "am",
        "broadcast",
        "-a",
        "com.oculus.vrpowermanager.automation_disable",
    ) in calls
    assert (
        "shell",
        "input",
        "keyevent",
        "KEYCODE_SLEEP",
    ) in calls
    assert ("forward", "--remove", "tcp:43210") in calls
    assert ("reverse", "--remove", "tcp:8765") in calls


def test_debug_forward_uses_an_adb_allocated_local_port(monkeypatch) -> None:
    bridge = QuestUsbBridge(adb=Path("/bin/true"), serial="quest")

    def command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return completed(*arguments, stdout="45678\n")

    monkeypatch.setattr(bridge, "_command", command)
    assert bridge._configure_debug_forward() == 45678  # noqa: SLF001
    assert bridge._configure_debug_forward() == 45678  # cached


def test_configure_removes_only_stale_owned_browser_forwards(
    monkeypatch,
) -> None:
    bridge = QuestUsbBridge(adb=Path("/bin/true"), serial="quest")
    calls: list[tuple[str, ...]] = []

    def command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments == ("forward", "--list"):
            return completed(
                *arguments,
                stdout=(
                    "quest tcp:41001 localabstract:chrome_devtools_remote\n"
                    "other tcp:41002 localabstract:chrome_devtools_remote\n"
                    "quest tcp:41003 localabstract:some_other_service\n"
                ),
            )
        return completed(*arguments)

    monkeypatch.setattr(bridge, "_select_device", lambda: "quest")
    monkeypatch.setattr(bridge, "_command", command)
    bridge.configure()

    assert ("forward", "--remove", "tcp:41001") in calls
    assert ("forward", "--remove", "tcp:41002") not in calls
    assert ("forward", "--remove", "tcp:41003") not in calls
    assert ("reverse", "tcp:8765", "tcp:8765") in calls


def test_browser_reset_releases_debug_forward_and_webvr_activity(
    monkeypatch,
) -> None:
    bridge = QuestUsbBridge(adb=Path("/bin/true"), serial="quest")
    bridge._debug_port = 43210  # noqa: SLF001
    calls: list[tuple[str, ...]] = []

    def command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return completed(*arguments)

    monkeypatch.setattr(bridge, "_command", command)
    monkeypatch.setattr("quest3_teleop.usb.time.sleep", lambda _seconds: None)

    bridge._reset_owned_browser()  # noqa: SLF001

    assert ("forward", "--remove", "tcp:43210") in calls
    assert (
        "shell",
        "am",
        "force-stop",
        "com.oculus.browser",
    ) in calls
    assert bridge._debug_port is None  # noqa: SLF001


def test_close_ends_managed_browser_tracking_before_removing_forward(
    monkeypatch,
) -> None:
    bridge = QuestUsbBridge(adb=Path("/bin/true"), serial="quest")
    bridge._configured = True  # noqa: SLF001
    bridge._debug_port = 43210  # noqa: SLF001
    bridge._tracking_url = (  # noqa: SLF001
        "http://localhost:8765/?_quest3_launch=123"
    )
    events: list[tuple[str, object]] = []

    class FakeAutomation:
        def __init__(self, *, debug_port: int) -> None:
            events.append(("automation", debug_port))

        def close_matching_pages(self, url: str) -> set[str]:
            events.append(("close_page", url))
            return {"page"}

    def command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        events.append(("adb", arguments))
        return completed(*arguments)

    monkeypatch.setattr(
        "quest3_teleop.usb.QuestBrowserAutomation",
        FakeAutomation,
    )
    monkeypatch.setattr(bridge, "_command", command)

    bridge.close()

    close_index = events.index(
        ("close_page", "http://localhost:8765/?_quest3_launch=123")
    )
    forward_index = events.index(("adb", ("forward", "--remove", "tcp:43210")))
    assert close_index < forward_index
    assert bridge._tracking_url is None  # noqa: SLF001


def test_missing_quest_error_has_actionable_usb_instructions(monkeypatch) -> None:
    bridge = QuestUsbBridge(adb=Path("/bin/true"))
    monkeypatch.setattr(
        "quest3_teleop.usb.subprocess.run",
        lambda *_args, **_kwargs: completed(
            "devices",
            stdout="List of devices attached\n\n",
        ),
    )
    monkeypatch.setattr("quest3_teleop.usb.quest_usb_present", lambda: False)

    with pytest.raises(QuestUsbError, match="USB data cable"):
        bridge.configure()
