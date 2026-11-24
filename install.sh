#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for arg in "$@"; do
    if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
        cat << 'EOF'
Penggunaan: sudo ./install.sh [OPSI]

Opsi:
  --only-monitor              Pasang hanya modul Fake Monitor (Kernel EDID + Autologin)
  --only-wifi                 Pasang hanya modul Auto Open Wi-Fi Fallback
  --connector <PORT>          Paksa nama port DRM tertentu (contoh: HDMI-A-1, DP-1)
  --resolution <WxH>          Resolusi EDID bawaan jika monitor tidak terpasang (default: 1920x1080)
  --force-generated-edid      Gunakan EDID generator bawaan walau monitor fisik sedang dicolok
  --autologin-user <USER>     Tentukan username untuk autologin desktop
  --no-autologin              Jangan ubah konfigurasi autologin Display Manager
EOF
        exit 0
    fi
done

if [[ "${EUID}" -ne 0 ]]; then
    echo "Error: jalankan installer ini dengan sudo:"
    echo "  sudo ./install.sh $*"
    exit 1
fi

INSTALL_MONITOR=1
INSTALL_WIFI=1
MONITOR_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --only-monitor)
            INSTALL_MONITOR=1
            INSTALL_WIFI=0
            shift
            ;;
        --only-wifi)
            INSTALL_MONITOR=0
            INSTALL_WIFI=1
            shift
            ;;
        -h|--help)
            cat << 'EOF'
Penggunaan: sudo ./install.sh [OPSI]

Opsi:
  --only-monitor              Pasang hanya modul Fake Monitor (Kernel EDID + Autologin)
  --only-wifi                 Pasang hanya modul Auto Open Wi-Fi Fallback
  --connector <PORT>          Paksa nama port DRM tertentu (contoh: HDMI-A-1, DP-1)
  --resolution <WxH>          Resolusi EDID bawaan jika monitor tidak terpasang (default: 1920x1080)
  --force-generated-edid      Gunakan EDID generator bawaan walau monitor fisik sedang dicolok
  --autologin-user <USER>     Tentukan username untuk autologin desktop
  --no-autologin              Jangan ubah konfigurasi autologin Display Manager
EOF
            exit 0
            ;;
        *)
            MONITOR_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ "${INSTALL_MONITOR}" -eq 1 ]]; then
    echo "=== [1/2] Memasang Modul Fake Monitor (Kernel DRM EDID) ==="
    python3 "${SCRIPT_DIR}/src/headless_kit.py" setup-monitor "${MONITOR_ARGS[@]}"
fi

if [[ "${INSTALL_WIFI}" -eq 1 ]]; then
    echo "=== [2/2] Memasang Modul Auto Open Wi-Fi Fallback ==="
    install -m 0755 -o root -g root "${SCRIPT_DIR}/bin/auto-open-wifi" /usr/local/sbin/auto-open-wifi

    cat > /etc/systemd/system/auto-open-wifi.service << 'EOF'
[Unit]
Description=Auto-connect to Open Wi-Fi when Ethernet LAN is disconnected
After=NetworkManager.service
Requires=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/auto-open-wifi
TimeoutStartSec=90
EOF

    cat > /etc/systemd/system/auto-open-wifi.timer << 'EOF'
[Unit]
Description=Periodic check for Open Wi-Fi fallback when LAN is disconnected

[Timer]
OnBootSec=15s
OnUnitActiveSec=30s
AccuracySec=5s
Unit=auto-open-wifi.service

[Install]
WantedBy=timers.target
EOF

    mkdir -p /etc/NetworkManager/dispatcher.d
    cat > /etc/NetworkManager/dispatcher.d/90-auto-open-wifi << 'EOF'
#!/bin/sh
ACTION="$2"
case "$ACTION" in
    up|down|connectivity-change|dhcp4-change)
        /bin/systemctl start --no-block auto-open-wifi.service >/dev/null 2>&1 || true
        ;;
esac
exit 0
EOF
    chown root:root /etc/NetworkManager/dispatcher.d/90-auto-open-wifi
    chmod 0755 /etc/NetworkManager/dispatcher.d/90-auto-open-wifi

    systemctl daemon-reload
    systemctl enable --now auto-open-wifi.timer
    echo "[wifi] auto-open-wifi.timer aktif dan siap memantau koneksi."
fi

echo ""
echo "Selesai! PC Linux ini siap dinyalakan tanpa monitor & otomatis fallback ke Open Wi-Fi saat tanpa LAN."
