#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RESET_KEY=false
case "${1:-}" in
  "") ;;
  --reset-key) RESET_KEY=true ;;
  *) echo "Usage: $0 [--reset-key]" >&2; exit 2 ;;
esac

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

if [[ "${RESET_KEY}" == true && -n "${ADB_VENDOR_KEYS:-}" ]]; then
  echo "Unset ADB_VENDOR_KEYS before using --reset-key." >&2
  exit 1
fi

if [[ ! -r /etc/udev/rules.d/51-quest3-teleop.rules ]]; then
  bash "${SCRIPT_DIR}/install_quest_udev_rule.sh"
fi

"${ADB}" kill-server
if [[ "${RESET_KEY}" == true ]]; then
  key_dir="${ANDROID_USER_HOME:-${HOME}/.android}"
  mkdir -p "${key_dir}"
  if [[ -e "${key_dir}/adbkey" || -e "${key_dir}/adbkey.pub" ]]; then
    backup_dir="$(mktemp -d "${key_dir}/adb-key-backup-XXXXXXXX")"
    for key in adbkey adbkey.pub; do
      [[ ! -e "${key_dir}/${key}" ]] || mv "${key_dir}/${key}" "${backup_dir}/"
    done
    echo "Previous ADB key backed up to ${backup_dir}"
  fi
fi
"${ADB}" start-server

echo "Connect the Quest with a USB data cable, wake and unlock it, then accept"
echo "'Allow USB debugging' and select 'Always allow from this computer'."

deadline=$((SECONDS + 120))
last_state=""
while (( SECONDS < deadline )); do
  devices="$("${ADB}" devices -l)"
  if awk 'NR > 1 && $2 == "device" { found=1 } END { exit !found }' <<<"${devices}"; then
    printf '%s\n' "${devices}"
    echo "Quest ADB authorization ready."
    exit 0
  fi
  state="disconnected"
  if awk 'NR > 1 && $2 == "unauthorized" { found=1 } END { exit !found }' <<<"${devices}"; then
    state="unauthorized"
  fi
  if [[ "${state}" != "${last_state}" ]]; then
    if [[ "${state}" == "unauthorized" ]]; then
      echo "Quest detected but unauthorized; accept the RSA prompt in the headset."
    else
      echo "Waiting for a Quest USB data connection..."
    fi
    last_state="${state}"
  fi
  sleep 2
done

"${ADB}" devices -l
echo "Authorization timed out. Reconnect USB and run this command again." >&2
exit 1
