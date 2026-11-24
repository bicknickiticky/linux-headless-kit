#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Error: jalankan dengan sudo: sudo ./uninstall.sh"
    exit 1
fi

echo "=== Menghapus Auto Open Wi-Fi Fallback ==="
systemctl disable --now auto-open-wifi.timer 2>/dev/null || true
systemctl stop auto-open-wifi.service 2>/dev/null || true
python3 -c '
from pathlib import Path
for p in [
    "/etc/systemd/system/auto-open-wifi.timer",
    "/etc/systemd/system/auto-open-wifi.service",
    "/etc/NetworkManager/dispatcher.d/90-auto-open-wifi",
    "/usr/local/sbin/auto-open-wifi",
]:
    Path(p).unlink(missing_ok=True)
'
systemctl daemon-reload

echo "=== Menghapus Fake Monitor Kernel EDID ==="
python3 "${SCRIPT_DIR}/src/headless_kit.py" uninstall-monitor

echo "Selesai! Konfigurasi telah dibersihkan (silakan reboot untuk mengembalikan deteksi monitor bawaan)."
