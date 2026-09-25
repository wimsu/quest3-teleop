#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

find_adb() {
  local candidate
  for candidate in \
    "${QUEST3_ADB:-}" \
    "${ANDROID_HOME:-}/platform-tools/adb" \
    "${ANDROID_SDK_ROOT:-}/platform-tools/adb" \
    "${HOME}/Android/Sdk/platform-tools/adb"; do
    if [[ -n "${candidate}" && -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  for candidate in "${HOME}"/Unity/Hub/Editor/*/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  command -v adb 2>/dev/null || true
}

ADB="$(find_adb)"
if [[ -z "${ADB}" ]]; then
  if command -v apt-get >/dev/null && command -v sudo >/dev/null; then
    echo "Installing Android ADB..."
    sudo apt-get update
    sudo apt-get install -y adb
    ADB="$(command -v adb)"
  else
    echo "adb was not found. Install Android platform-tools or set QUEST3_ADB=/path/to/adb." >&2
    exit 1
  fi
fi

if [[ ! -r /etc/udev/rules.d/51-quest3-teleop.rules ]]; then
  bash "${SCRIPT_DIR}/install_quest_udev_rule.sh"
fi

"${ADB}" kill-server
"${ADB}" start-server

echo "Connect the Quest with a USB data cable, wake and unlock it, then accept"
echo "'Allow USB debugging' and select 'Always allow from this computer'."

deadline=$((SECONDS + 120))
while (( SECONDS < deadline )); do
  devices="$("${ADB}" devices -l)"
  printf '\rWaiting for Quest authorization...'
  if awk 'NR > 1 && $2 == "device" { found=1 } END { exit !found }' <<<"${devices}"; then
    printf '\n%s\n' "${devices}"
    echo "Quest ADB authorization ready."
    exit 0
  fi
  sleep 2
done

printf '\n'
"${ADB}" devices -l
echo "Authorization timed out. Reconnect USB and run this command again." >&2
exit 1
