#!/usr/bin/env bash
set -euo pipefail

RULE_PATH="/etc/udev/rules.d/51-quest3-teleop.rules"
GROUP_CLAUSE=""
if getent group plugdev >/dev/null 2>&1; then
  GROUP_CLAUSE=', GROUP="plugdev"'
fi
RULE='SUBSYSTEM=="usb", ATTR{idVendor}=="2833", MODE="0660"'"${GROUP_CLAUSE}"', TAG+="uaccess"'

printf '%s\n' "${RULE}" | sudo tee "${RULE_PATH}" >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=2833

echo "Installed ${RULE_PATH}"
echo "Unplug and reconnect the Quest USB cable once, then verify with: adb devices -l"
