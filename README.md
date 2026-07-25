# Quest3 Teleop

Robot-independent Meta Quest 3 hand tracking for Linux teleoperation systems.

```text
Quest WebXR Hands -> USB ADB reverse -> HandFrame
```

The package streams 25 joints per hand, joint radii, and the headset pose in
the WebXR `local` frame. Robot retargeting and control belong in downstream
repositories.

## Setup

Requirements: Linux, Python 3.10+, Android `adb`, a USB data cable, and a
developer-mode Quest 3.

```bash
git clone https://github.com/wimsu/quest3-teleop.git
cd quest3-teleop

python3 -m venv .venv
.venv/bin/python -m pip install -e .
bash scripts/install_quest_udev_rule.sh
```

Reconnect the headset, accept its USB debugging prompt, and verify:

```bash
adb devices -l
```

`adb` may also be selected with `QUEST3_ADB`, `ANDROID_HOME`,
`ANDROID_SDK_ROOT`, or `--adb`.

## Usage

```bash
.venv/bin/quest3-teleop probe --seconds 30
```

The command starts tracking, reports frame health, and restores the Quest
Browser and power state on exit. Use `--port` to change the local port and
`--serial` when multiple ADB devices are attached.

## Python API

```python
from quest3_teleop import Quest3UsbSession

with Quest3UsbSession() as quest:
    while True:
        frame = quest.poll()
        if frame is not None:
            consume(frame)
```

`HandFrame` is immutable and contains desktop-monotonic receive time,
browser-local sequence time, head pose, and named left/right hand joints.
See [docs/protocol.md](docs/protocol.md) for the data contract.

## Development

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```
