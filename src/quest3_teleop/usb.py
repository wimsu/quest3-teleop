"""ADB reverse setup for a physically connected Quest."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .browser import BrowserStartResult, QuestBrowserAutomation, QuestBrowserError


class QuestUsbError(RuntimeError):
    pass


def quest_usb_present() -> bool:
    """Return whether Linux can see a Meta/Oculus USB device at all."""

    for vendor_path in Path("/sys/bus/usb/devices").glob("*/idVendor"):
        try:
            if vendor_path.read_text().strip().lower() == "2833":
                return True
        except OSError:
            continue
    return False


def find_adb() -> Path | None:
    """Find Android platform-tools without requiring a Unity project."""

    explicit = os.environ.get("QUEST3_ADB")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if executable := shutil.which("adb"):
        candidates.append(Path(executable))
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if root := os.environ.get(variable):
            candidates.append(Path(root).expanduser() / "platform-tools" / "adb")
    candidates.append(Path.home() / "Android" / "Sdk" / "platform-tools" / "adb")
    # Compatibility with this workstation's existing Android SDK install.
    # Nothing from Unity is imported or executed; this is the standard adb
    # binary shipped inside an already-installed Android SDK directory.
    candidates.extend(
        sorted(
            Path.home().glob(
                "Unity/Hub/Editor/*/Editor/Data/PlaybackEngines/"
                "AndroidPlayer/SDK/platform-tools/adb"
            ),
            reverse=True,
        )
    )
    return next(
        (
            path.resolve()
            for path in candidates
            if path.is_file() and os.access(path, os.X_OK)
        ),
        None,
    )


class QuestUsbBridge:
    def __init__(
        self,
        *,
        port: int = 8765,
        adb: str | Path | None = None,
        serial: str | None = None,
    ) -> None:
        path = Path(adb).expanduser().resolve() if adb is not None else find_adb()
        if path is None:
            raise QuestUsbError("adb not found; install Android platform-tools or set QUEST3_ADB")
        self.adb = path
        self.port = int(port)
        self.serial = serial
        self._configured = False
        self._debug_port: int | None = None
        self._power_prepared = False
        self._tracking_url: str | None = None

    def _command(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = [str(self.adb)]
        if self.serial:
            command.extend(("-s", self.serial))
        command.extend(arguments)
        return subprocess.run(command, check=check, capture_output=True, text=True)

    def _select_device(self) -> str:
        result = subprocess.run(
            [str(self.adb), "devices", "-l"],
            check=True,
            capture_output=True,
            text=True,
        )
        devices = []
        unauthorized = []
        for line in result.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2:
                continue
            if fields[1] == "device":
                devices.append(fields[0])
            elif fields[1] == "unauthorized":
                unauthorized.append(fields[0])
        if self.serial:
            if self.serial not in devices:
                raise QuestUsbError(f"ADB device {self.serial!r} is not connected and authorized")
            return self.serial
        if unauthorized:
            raise QuestUsbError(
                "Quest is unauthorized; unlock it and accept the USB debugging prompt"
            )
        if len(devices) != 1:
            if not devices and quest_usb_present():
                raise QuestUsbError(
                    "Quest is physically connected but ADB cannot access it. "
                    "Install the Linux Quest udev rule, then unplug and reconnect USB."
                )
            if not devices:
                raise QuestUsbError(
                    "Quest is not visible to ADB. Connect it with a USB data cable, "
                    "wake and unlock the headset, then accept USB debugging if prompted."
                )
            raise QuestUsbError(
                f"expected one authorized Quest over USB, found {len(devices)}"
            )
        self.serial = devices[0]
        return self.serial

    def configure(self) -> str:
        serial = self._select_device()
        self._remove_stale_browser_forwards(serial)
        self._command("reverse", f"tcp:{self.port}", f"tcp:{self.port}")
        self._configured = True
        return serial

    def _remove_stale_browser_forwards(self, serial: str) -> None:
        """Remove DevTools routes left by an interrupted owned Browser run."""

        result = self._command("forward", "--list", check=False)
        if result.returncode != 0:
            return
        for line in result.stdout.splitlines():
            fields = line.split()
            if (
                len(fields) != 3
                or fields[0] != serial
                or not fields[1].startswith("tcp:")
                or fields[2] != "localabstract:chrome_devtools_remote"
            ):
                continue
            self._command(
                "forward",
                "--remove",
                fields[1],
                check=False,
            )

    def _prepare_unworn_tracking(self) -> None:
        """Keep a USB-powered Quest awake when it is resting on the operator."""

        if not self._configured:
            self.configure()
        operations = (
            ("shell", "am", "broadcast", "-a", "com.oculus.vrpowermanager.prox_close"),
            ("shell", "svc", "power", "stayon", "usb"),
            ("shell", "input", "keyevent", "KEYCODE_WAKEUP"),
        )
        for arguments in operations:
            result = self._command(*arguments, check=False)
            if result.returncode != 0:
                raise QuestUsbError(
                    f"could not prepare Quest for unworn tracking: {result.stderr.strip()}"
                )
        self._power_prepared = True

    def _reassert_unworn_tracking(self) -> None:
        """Re-arm the proximity override after Quest creates a new XR activity."""

        if not self._configured:
            self.configure()
        # prox_close is stateful. Toggling the automation state first makes
        # the subsequent broadcast observable to a newly created WebVRActivity
        # instead of being treated as an already-applied no-op.
        self._command(
            "shell",
            "am",
            "broadcast",
            "-a",
            "com.oculus.vrpowermanager.automation_disable",
            check=False,
        )
        self._prepare_unworn_tracking()

    def _configure_debug_forward(self) -> int:
        if self._debug_port is not None:
            return self._debug_port
        result = self._command(
            "forward",
            "tcp:0",
            "localabstract:chrome_devtools_remote",
            check=False,
        )
        if result.returncode != 0:
            raise QuestUsbError(
                f"could not connect to Quest Browser debugging: {result.stderr.strip()}"
            )
        try:
            self._debug_port = int(result.stdout.strip())
        except ValueError as exc:
            raise QuestUsbError(
                f"adb did not return a browser debugging port: {result.stdout.strip()!r}"
            ) from exc
        return self._debug_port

    def _open_browser(self, url: str | None = None) -> None:
        if not self._configured:
            self.configure()
        target = url or f"http://localhost:{self.port}/"
        result = self._command(
            "shell",
            "am",
            "start",
            "-W",
            "-a",
            "android.intent.action.VIEW",
            "-p",
            "com.oculus.browser",
            "-d",
            target,
            check=False,
        )
        if result.returncode != 0:
            raise QuestUsbError(f"could not open Quest Browser: {result.stderr.strip()}")

    def _reset_owned_browser(self) -> None:
        """Remove stale Browser/WebVR state before a new tracker session."""

        if self._debug_port is not None:
            self._command(
                "forward",
                "--remove",
                f"tcp:{self._debug_port}",
                check=False,
            )
            self._debug_port = None
        self._command(
            "shell",
            "am",
            "force-stop",
            "com.oculus.browser",
            check=False,
        )
        # Give Android enough time to release WebVRActivity and its hand
        # tracking ownership before relaunching Browser.
        time.sleep(0.35)

    def start_tracking(
        self,
        url: str | None = None,
        *,
        timeout_s: float = 20.0,
        attempts: int = 2,
    ) -> BrowserStartResult:
        """Wake Quest and enter one clean tracker-only WebXR session.

        Quest Browser occasionally retains an ended WebVRActivity after a tab
        refresh or an interrupted desktop process.  Browser is dedicated to
        this tracker while the bridge owns it, so every launch starts from a
        clean process and one automatic retry is performed before surfacing an
        error to the operator.
        """

        if not self._configured:
            self.configure()
        if attempts < 1:
            raise ValueError("attempts must be positive")
        base_target = url or f"http://localhost:{self.port}/"
        self._prepare_unworn_tracking()
        self._reset_owned_browser()
        last_error: QuestBrowserError | None = None
        for attempt in range(attempts):
            parts = urlsplit(base_target)
            query = [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key != "_quest3_launch"
            ]
            query.append(("_quest3_launch", str(time.monotonic_ns())))
            target = urlunsplit(
                (
                    parts.scheme,
                    parts.netloc,
                    parts.path,
                    urlencode(query),
                    parts.fragment,
                )
            )
            self._tracking_url = target
            self._open_browser(target)
            try:
                automation = QuestBrowserAutomation(
                    debug_port=self._configure_debug_forward()
                )
                automation.navigate_page(target)
                return automation.start_page(
                    target,
                    after_session_request=self._reassert_unworn_tracking,
                    timeout_s=timeout_s,
                )
            except QuestBrowserError as exc:
                last_error = exc
                self._stop_tracking()
                if attempt + 1 < attempts:
                    self._prepare_unworn_tracking()

        assert last_error is not None
        try:
            self._select_device()
        except (QuestUsbError, subprocess.SubprocessError) as device_exc:
            raise QuestUsbError(
                f"Quest USB became unavailable during browser startup: {device_exc}"
            ) from last_error
        raise QuestUsbError(
            f"{last_error}; Quest Browser was reset and retried automatically"
        ) from last_error

    def _stop_tracking(self) -> None:
        """End the managed WebXR session and release Browser ownership."""

        if self._tracking_url is not None and self._debug_port is not None:
            try:
                automation = QuestBrowserAutomation(debug_port=self._debug_port)
                automation.close_matching_pages(self._tracking_url)
            except (QuestBrowserError, OSError):
                # ADB or Browser may already have disappeared.
                pass
        self._tracking_url = None
        # Force-stop is the final ownership boundary. It covers Ctrl+C during
        # page startup, a crashed renderer, and Browser versions that keep a
        # WebVRActivity alive after its tab is closed.
        self._reset_owned_browser()

    def _restore_power_behavior(self) -> None:
        if not self._power_prepared:
            return
        self._command("shell", "svc", "power", "stayon", "false", check=False)
        self._command(
            "shell",
            "am",
            "broadcast",
            "-a",
            "com.oculus.vrpowermanager.automation_disable",
            check=False,
        )
        # Tracking-only sessions deliberately kept the display awake even when
        # Quest was resting on the operator. Put the display to sleep now; the
        # next start_tracking() call wakes it again.
        self._command(
            "shell",
            "input",
            "keyevent",
            "KEYCODE_SLEEP",
            check=False,
        )
        self._power_prepared = False

    def close(self) -> None:
        self._stop_tracking()
        self._restore_power_behavior()
        if self._debug_port is not None:
            self._command("forward", "--remove", f"tcp:{self._debug_port}", check=False)
            self._debug_port = None
        if self._configured:
            self._command("reverse", "--remove", f"tcp:{self.port}", check=False)
            self._configured = False

    def __enter__(self) -> "QuestUsbBridge":
        self.configure()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["QuestUsbBridge", "QuestUsbError", "find_adb", "quest_usb_present"]
