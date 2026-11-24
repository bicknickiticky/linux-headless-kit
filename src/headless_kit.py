#!/usr/bin/env python3
"""
linux-headless-kit core library & CLI:
1. Fake Monitor (Kernel DRM EDID + Connector Force Enable + Initramfs + Autologin + No-DPMS)
2. Auto Open Wi-Fi (NetworkManager automatic open/OWE Wi-Fi fallback when Ethernet LAN is disconnected)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

INTERNAL_CONNECTOR_PREFIXES = ("eDP-", "LVDS-", "DSI-")
PROFILE_PREFIX = "auto-open-"


# ============================================================================
# 1. EDID GENERATOR & VALIDATOR
# ============================================================================

def is_valid_edid(data: bytes) -> bool:
    if len(data) < 128 or len(data) % 128 != 0:
        return False
    if data[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return False
    for offset in range(0, len(data), 128):
        if sum(data[offset : offset + 128]) % 256 != 0:
            return False
    return True


def _build_dtd(width: int, height: int, refresh: int = 60) -> bytes:
    """Build an 18-byte VESA Detailed Timing Descriptor (DTD)."""
    presets = {
        # (h_blank, h_fp, h_sync, v_blank, v_fp, v_sync, pclk_10khz)
        (1920, 1080, 60): (280, 88, 44, 45, 4, 5, 14850),
        (2560, 1080, 60): (160, 48, 32, 31, 3, 10, 18558),
        (2560, 1440, 60): (160, 48, 32, 41, 3, 5, 24150),
        (1280, 720, 60): (370, 110, 40, 30, 5, 5, 7425),
    }
    if (width, height, refresh) in presets:
        h_blank, h_fp, h_sync, v_blank, v_fp, v_sync, pclk_10khz = presets[(width, height, refresh)]
    else:
        # Reduced-blanking timing approximation for custom resolutions
        h_blank, h_fp, h_sync = 160, 48, 32
        v_blank = max(25, int(height * 0.03))
        v_fp, v_sync = 3, 5
        pclk_hz = (width + h_blank) * (height + v_blank) * refresh
        pclk_10khz = max(1, round(pclk_hz / 10000))

    h_mm = min(4095, max(300, round(width * 0.27)))
    v_mm = min(4095, max(200, round(height * 0.27)))

    dtd = bytearray(18)
    dtd[0] = pclk_10khz & 0xFF
    dtd[1] = (pclk_10khz >> 8) & 0xFF
    dtd[2] = width & 0xFF
    dtd[3] = h_blank & 0xFF
    dtd[4] = (((width >> 8) & 0x0F) << 4) | ((h_blank >> 8) & 0x0F)
    dtd[5] = height & 0xFF
    dtd[6] = v_blank & 0xFF
    dtd[7] = (((height >> 8) & 0x0F) << 4) | ((v_blank >> 8) & 0x0F)
    dtd[8] = h_fp & 0xFF
    dtd[9] = h_sync & 0xFF
    dtd[10] = ((v_fp & 0x0F) << 4) | (v_sync & 0x0F)
    dtd[11] = (
        (((h_fp >> 8) & 0x03) << 6)
        | (((h_sync >> 8) & 0x03) << 4)
        | (((v_fp >> 4) & 0x03) << 2)
        | ((v_sync >> 4) & 0x03)
    )
    dtd[12] = h_mm & 0xFF
    dtd[13] = v_mm & 0xFF
    dtd[14] = (((h_mm >> 8) & 0x0F) << 4) | ((v_mm >> 8) & 0x0F)
    dtd[15] = 0x00  # horizontal border
    dtd[16] = 0x00  # vertical border
    dtd[17] = 0x1E  # Digital Separate Sync, +VSync, +HSync, non-interlaced
    return bytes(dtd)


def generate_edid(width: int = 1920, height: int = 1080, refresh: int = 60) -> bytes:
    """Generate a valid 128-byte EDID v1.4 digital monitor block."""
    edid = bytearray(128)
    # 0..7: Fixed header
    edid[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    # 8..9: Manufacturer ID "LHK" (L=12, H=8, K=11) -> (12<<10)|(8<<5)|11 = 0x310B
    edid[8:10] = (0x310B).to_bytes(2, "big")
    # 10..11: Product code
    edid[10:12] = (0x1080).to_bytes(2, "little")
    # 12..15: Serial number
    edid[12:16] = (1).to_bytes(4, "little")
    # 16..17: Manufacture week 1, year 2026 (2026 - 1990 = 36)
    edid[16] = 1
    edid[17] = 36
    # 18..19: EDID version 1.4
    edid[18] = 1
    edid[19] = 4
    # 20..24: Basic display parameters (Digital 8bpc, sRGB, preferred timing in DTD1)
    edid[20] = 0xA0  # Digital input, 8-bit color depth
    edid[21] = min(255, max(30, round(width * 0.027)))
    edid[22] = min(255, max(20, round(height * 0.027)))
    edid[23] = 120  # Gamma 2.20
    edid[24] = 0x06  # RGB 4:4:4 + Preferred Timing Mode includes native pixel format
    # 25..34: Standard sRGB chromaticity coordinates
    edid[25:35] = bytes([0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54])
    # 35..37: Established timings (640x480@60, 800x600@60, 1024x768@60)
    edid[35:38] = bytes([0x21, 0x08, 0x00])
    # 38..53: Standard timings (1920x1080@60 16:9 -> (1920/8 - 31)=0xD1, 0xC0; unused=0x01,0x01)
    edid[38:40] = bytes([0xD1, 0xC0])
    for i in range(40, 54, 2):
        edid[i : i + 2] = b"\x01\x01"

    # 54..71: Descriptor 1 - Preferred Detailed Timing
    edid[54:72] = _build_dtd(width, height, refresh)

    # 72..89: Descriptor 2 - Monitor Range Limits (48-75 Hz, 30-160 kHz, max 300 MHz)
    edid[72:90] = bytes([
        0x00, 0x00, 0x00, 0xFD, 0x00,
        48, 75, 30, 160, 30, 0x00,
        0x0A, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20,
    ])

    # 90..107: Descriptor 3 - Monitor Name ("Headless Mon")
    name = f"Headless {height}p\n"[:13].ljust(13, " ").encode("ascii")
    edid[90:108] = b"\x00\x00\x00\xfc\x00" + name

    # 108..125: Descriptor 4 - Dummy / Serial string ("LHK-0001")
    serial = "LHK-0001\n".ljust(13, " ").encode("ascii")
    edid[108:126] = b"\x00\x00\x00\xff\x00" + serial

    # 126: Extension flag = 0
    edid[126] = 0
    # 127: Checksum
    edid[127] = (256 - (sum(edid[:127]) % 256)) % 256
    return bytes(edid)


# ============================================================================
# 2. DRM CONNECTOR DISCOVERY & GRUB UPDATER
# ============================================================================

def scan_drm_ports(sys_drm: Path = Path("/sys/class/drm")) -> list[dict]:
    ports: list[dict] = []
    if not sys_drm.exists():
        return ports
    for p in sorted(sys_drm.iterdir()):
        m = re.match(r"^(card\d+)-(.+)$", p.name)
        if not m or not (p / "status").exists():
            continue
        connector = m.group(2)
        if connector.startswith("Writeback-"):
            continue
        try:
            status = (p / "status").read_text().strip()
        except Exception:
            status = "unknown"
        edid_bytes = b""
        edid_path = p / "edid"
        if edid_path.exists():
            try:
                edid_bytes = edid_path.read_bytes()
            except Exception:
                edid_bytes = b""
        ports.append({
            "card_port": p.name,
            "connector": connector,
            "status": status,
            "edid": edid_bytes,
            "edid_len": len(edid_bytes),
            "path": str(p),
        })
    return ports


def choose_connector(ports: list[dict], requested: str | None = None) -> dict:
    if not ports:
        raise RuntimeError("Tidak ditemukan port DRM di /sys/class/drm")

    if requested:
        for p in ports:
            if p["connector"] == requested or p["card_port"] == requested:
                return p
        raise RuntimeError(f"Port '{requested}' tidak ditemukan. Pilihan: {[p['connector'] for p in ports]}")

    external = [p for p in ports if not p["connector"].startswith(INTERNAL_CONNECTOR_PREFIXES)]
    pool = external or ports

    # 1. Prefer currently connected external port
    connected = [p for p in pool if p["status"] == "connected"]
    if connected:
        return connected[0]

    # 2. When completely headless, prefer HDMI-A-* first, then DP-*, then first external
    hdmi = [p for p in pool if p["connector"].startswith("HDMI")]
    if hdmi:
        return hdmi[0]
    dp = [p for p in pool if p["connector"].startswith("DP")]
    if dp:
        return dp[0]
    return pool[0]


def remove_grub_params(grub_text: str) -> str:
    """Remove any existing drm.edid_firmware=... and video=...:e/D tokens from GRUB_CMDLINE_LINUX_DEFAULT."""
    def _clean_match(match: re.Match) -> str:
        prefix, val, suffix = match.group(1), match.group(2), match.group(3)
        tokens = val.split()
        kept = [
            t for t in tokens
            if not t.startswith("drm.edid_firmware=")
            and not re.match(r"^video=[A-Za-z0-9_-]+:[eD]$", t)
        ]
        return f'{prefix}{" ".join(kept)}{suffix}'

    return re.sub(
        r'^(GRUB_CMDLINE_LINUX_DEFAULT=["\'])(.*)(["\']\s*)$',
        _clean_match,
        grub_text,
        flags=re.MULTILINE,
    )


def update_grub_content(grub_text: str, connector: str, fw_rel_path: str) -> str:
    cleaned = remove_grub_params(grub_text)
    inject = f"drm.edid_firmware={connector}:{fw_rel_path} video={connector}:e"

    def _add_match(match: re.Match) -> str:
        prefix, val, suffix = match.group(1), match.group(2).strip(), match.group(3)
        combined = f"{val} {inject}".strip()
        return f"{prefix}{combined}{suffix}"

    updated, count = re.subn(
        r'^(GRUB_CMDLINE_LINUX_DEFAULT=["\'])(.*)(["\']\s*)$',
        _add_match,
        cleaned,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        updated = cleaned.rstrip("\n") + f'\nGRUB_CMDLINE_LINUX_DEFAULT="{inject}"\n'
    return updated


# ============================================================================
# 3. AUTO OPEN WI-FI PARSER & RANKER
# ============================================================================

def split_nmcli_escaped(line: str) -> list[str]:
    fields: list[str] = []
    buf: list[str] = []
    esc = False
    for ch in line:
        if esc:
            buf.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    fields.append("".join(buf))
    return fields


def valid_ssid(ssid: str) -> bool:
    if not ssid or not ssid.strip():
        return False
    raw = ssid.encode("utf-8", errors="ignore")
    if not (1 <= len(raw) <= 32):
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in ssid):
        return False
    return True


def make_profile_name(ssid: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", ssid).strip("_")[:18] or "wifi"
    digest = hashlib.sha256(ssid.encode("utf-8")).hexdigest()[:8]
    return f"{PROFILE_PREFIX}{slug}-{digest}"


def rank_open_networks(nmcli_lines: list[str]) -> list[tuple[str, int]]:
    best: dict[str, int] = {}
    for line in nmcli_lines:
        if not line.strip():
            continue
        parts = split_nmcli_escaped(line)
        if len(parts) < 5:
            continue
        _in_use, ssid, _bssid, security, sig_str = (
            parts[0],
            parts[1],
            parts[2],
            parts[3].strip(),
            parts[4].strip(),
        )
        if not valid_ssid(ssid):
            continue
        if security not in ("", "--", "OWE"):
            continue
        try:
            signal = int(sig_str)
        except ValueError:
            signal = 0
        if ssid not in best or signal > best[ssid]:
            best[ssid] = signal
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


# ============================================================================
# 4. SYSTEM INSTALLER / UNINSTALLER HELPERS (FAKE MONITOR)
# ============================================================================

def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = Path(f"{path}.backup.{ts}")
    shutil.copy2(path, bak)
    print(f"[backup] {path} -> {bak}")
    return bak


def rebuild_initramfs_with_edid(fw_rel_path: str) -> None:
    """Bundle /lib/firmware/<fw_rel_path> into initramfs across Ubuntu/Debian, Arch, or Fedora."""
    # Proactively write dracut drop-in so future distro upgrades (e.g. Ubuntu 24.04 -> 26.04 dracut) keep EDID
    dracut_d = Path("/etc/dracut.conf.d")
    dracut_d.mkdir(parents=True, exist_ok=True)
    (dracut_d / "10-headless-edid.conf").write_text(f'install_items+=" /usr/lib/firmware/{fw_rel_path} "\n')

    if shutil.which("update-initramfs") and Path("/etc/initramfs-tools").is_dir():
        hook = Path("/etc/initramfs-tools/hooks/edid")
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "#!/bin/sh\n"
            'PREREQ=""\n'
            'case "$1" in prereqs) echo "$PREREQ"; exit 0 ;; esac\n'
            ". /usr/share/initramfs-tools/hook-functions\n"
            f'add_firmware "{fw_rel_path}" || true\n'
            "exit 0\n"
        )
        hook.chmod(0o755)
        subprocess.run(["update-initramfs", "-u"], check=True)
    elif shutil.which("mkinitcpio"):
        conf_d = Path("/etc/mkinitcpio.conf.d")
        conf_d.mkdir(parents=True, exist_ok=True)
        (conf_d / "10-headless-edid.conf").write_text(f'FILES+=("/usr/lib/firmware/{fw_rel_path}")\n')
        subprocess.run(["mkinitcpio", "-P"], check=True)
    elif shutil.which("dracut"):
        subprocess.run(["dracut", "--regenerate-all", "--force"], check=True)
    else:
        print("[warn] Tidak menemukan update-initramfs / mkinitcpio / dracut; lewati rebuild initramfs.")


def update_bootloader() -> None:
    if shutil.which("update-grub"):
        subprocess.run(["update-grub"], check=True)
    elif shutil.which("grub-mkconfig") and Path("/boot/grub/grub.cfg").exists():
        subprocess.run(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], check=True)
    elif shutil.which("grub2-mkconfig") and Path("/boot/grub2/grub.cfg").exists():
        subprocess.run(["grub2-mkconfig", "-o", "/boot/grub2/grub.cfg"], check=True)
    else:
        print("[warn] Bootloader updater GRUB tidak ditemukan; perbarui bootloader secara manual.")


def detect_default_user() -> str | None:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return sudo_user
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 60000 and p.pw_dir.startswith("/home/"):
            return p.pw_name
    return None


def pick_x11_session(existing_names: list[str] | set[str]) -> str | None:
    preferred_order = [
        "plasmax11.desktop",  # KDE Plasma 6 X11 (Ubuntu 26.04+)
        "plasma.desktop",     # KDE Plasma 5 X11 (Ubuntu 24.04)
        "ubuntu-xorg.desktop",
        "ubuntu.desktop",
        "xfce.desktop",
        "cinnamon.desktop",
        "mate.desktop",
    ]
    names_set = set(existing_names)
    for cand in preferred_order:
        if cand in names_set:
            return cand
    sorted_names = sorted(names_set)
    return sorted_names[0] if sorted_names else None


def configure_autologin(user: str) -> None:
    dm_file = Path("/etc/X11/default-display-manager")
    dm = dm_file.read_text().strip() if dm_file.exists() else ""

    xsessions = sorted(Path("/usr/share/xsessions").glob("*.desktop")) if Path("/usr/share/xsessions").exists() else []
    session_name = pick_x11_session([x.name for x in xsessions])

    if "sddm" in dm or Path("/etc/sddm.conf.d").is_dir():
        sddm_dir = Path("/etc/sddm.conf.d")
        sddm_dir.mkdir(parents=True, exist_ok=True)
        kde_conf = sddm_dir / "kde_settings.conf"
        target = kde_conf if kde_conf.exists() else (sddm_dir / "50-headless-autologin.conf")
        backup_file(target)
        sess = session_name or "plasma.desktop"
        if target.exists() and "[Autologin]" in target.read_text():
            txt = target.read_text()
            txt = re.sub(r"(?m)^Session=.*$", f"Session={sess}", txt)
            txt = re.sub(r"(?m)^User=.*$", f"User={user}", txt)
            target.write_text(txt)
        else:
            target.write_text(f"[Autologin]\nRelogin=false\nSession={sess}\nUser={user}\n")
        print(f"[autologin] SDDM dikonfigurasi untuk user '{user}' ({sess})")
    elif "gdm" in dm or Path("/etc/gdm3/custom.conf").exists() or Path("/etc/gdm/custom.conf").exists():
        gdm_conf = Path("/etc/gdm3/custom.conf") if Path("/etc/gdm3/custom.conf").exists() else Path("/etc/gdm/custom.conf")
        backup_file(gdm_conf)
        txt = gdm_conf.read_text() if gdm_conf.exists() else "[daemon]\n"
        txt = re.sub(r"(?m)^#?\s*WaylandEnable=.*$", "WaylandEnable=false", txt)
        if "AutomaticLoginEnable=" in txt:
            txt = re.sub(r"(?m)^#?\s*AutomaticLoginEnable=.*$", "AutomaticLoginEnable=true", txt)
            txt = re.sub(r"(?m)^#?\s*AutomaticLogin=.*$", f"AutomaticLogin={user}", txt)
        elif "[daemon]" in txt:
            txt = txt.replace("[daemon]", f"[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin={user}\nWaylandEnable=false", 1)
        gdm_conf.write_text(txt)
        print(f"[autologin] GDM dikonfigurasi untuk user '{user}' (Xorg)")
    elif "lightdm" in dm or Path("/etc/lightdm").is_dir():
        ldm_dir = Path("/etc/lightdm/lightdm.conf.d")
        ldm_dir.mkdir(parents=True, exist_ok=True)
        target = ldm_dir / "50-headless-autologin.conf"
        backup_file(target)
        target.write_text(f"[Seat:*]\nautologin-user={user}\nautologin-user-timeout=0\n")
        print(f"[autologin] LightDM dikonfigurasi untuk user '{user}'")


def cmd_setup_monitor(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        print("Error: jalankan dengan sudo/root.", file=sys.stderr)
        return 1

    ports = scan_drm_ports()
    chosen = choose_connector(ports, args.connector)
    connector = chosen["connector"]
    print(f"[monitor] Port terpilih: {connector} ({chosen['card_port']}, status={chosen['status']})")

    if args.edid_file:
        edid_bytes = Path(args.edid_file).read_bytes()
        source_desc = f"file kustom {args.edid_file}"
    elif chosen["edid"] and is_valid_edid(chosen["edid"]) and not args.force_generated_edid:
        edid_bytes = chosen["edid"]
        source_desc = f"clone EDID asli monitor pada {chosen['card_port']} ({len(edid_bytes)} byte)"
    else:
        m = re.match(r"^(\d+)x(\d+)$", args.resolution)
        if not m:
            raise ValueError(f"Format resolusi tidak valid: {args.resolution} (contoh: 1920x1080)")
        w, h = int(m.group(1)), int(m.group(2))
        edid_bytes = generate_edid(w, h, 60)
        source_desc = f"EDID bawaan {w}x{h}@60Hz (128 byte)"

    if not is_valid_edid(edid_bytes):
        raise ValueError("Data EDID tidak valid (header/checksum gagal).")

    fw_rel = f"edid/{args.edid_name}"
    fw_dest = Path("/lib/firmware") / fw_rel
    fw_dest.parent.mkdir(parents=True, exist_ok=True)
    backup_file(fw_dest)
    fw_dest.write_bytes(edid_bytes)
    fw_dest.chmod(0o644)
    print(f"[monitor] Firmware EDID dipasang ke {fw_dest} ({source_desc})")

    grub_path = Path("/etc/default/grub")
    if not grub_path.exists():
        raise FileNotFoundError("/etc/default/grub tidak ditemukan.")
    backup_file(grub_path)
    grub_path.write_text(update_grub_content(grub_path.read_text(), connector, fw_rel))
    grub_d = Path("/etc/default/grub.d")
    if grub_d.is_dir():
        dropin = grub_d / "98-headless-monitor.cfg"
        backup_file(dropin)
        dropin.write_text(
            "# Survives OS release upgrades even if /etc/default/grub is replaced\n"
            'case " $GRUB_CMDLINE_LINUX_DEFAULT " in\n'
            '    *" drm.edid_firmware="*) ;;\n'
            f'    *) GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT drm.edid_firmware={connector}:{fw_rel} video={connector}:e" ;;\n'
            "esac\n"
        )
    print(f"[monitor] Parameter kernel ditambahkan ke {grub_path}: drm.edid_firmware={connector}:{fw_rel} video={connector}:e")

    # Anti-DPMS Xorg config
    xorg_dir = Path("/etc/X11/xorg.conf.d")
    xorg_dir.mkdir(parents=True, exist_ok=True)
    nodpms = xorg_dir / "10-headless-nodpms.conf"
    backup_file(nodpms)
    nodpms.write_text(
        'Section "ServerFlags"\n'
        '    Option "BlankTime" "0"\n'
        '    Option "StandbyTime" "0"\n'
        '    Option "SuspendTime" "0"\n'
        '    Option "OffTime" "0"\n'
        'EndSection\n'
    )
    print(f"[monitor] Konfigurasi anti-DPMS Xorg dipasang di {nodpms}")

    if not args.no_autologin:
        user = args.autologin_user or detect_default_user()
        if user:
            configure_autologin(user)

    rebuild_initramfs_with_edid(fw_rel)
    update_bootloader()

    # Live runtime activation so it works immediately even before reboot
    edid_param = Path("/sys/module/drm/parameters/edid_firmware")
    if edid_param.exists():
        try:
            edid_param.write_text(f"{connector}:{fw_rel}")
            status_file = Path(chosen["path"]) / "status"
            if status_file.exists():
                status_file.write_text("on\n")
            print(f"[monitor] Aktivasi live kernel DRM untuk {connector} berhasil.")
        except Exception as exc:
            print(f"[info] Aktivasi live dilewati (aktif penuh saat reboot): {exc}")

    return 0


def cmd_uninstall_monitor(_args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        print("Error: jalankan dengan sudo/root.", file=sys.stderr)
        return 1

    grub_path = Path("/etc/default/grub")
    if grub_path.exists():
        backup_file(grub_path)
        grub_path.write_text(remove_grub_params(grub_path.read_text()))
        update_bootloader()
        print("[uninstall] Parameter fake monitor dihapus dari /etc/default/grub")

    for path_str in [
        "/etc/default/grub.d/98-headless-monitor.cfg",
        "/etc/initramfs-tools/hooks/edid",
        "/etc/mkinitcpio.conf.d/10-headless-edid.conf",
        "/etc/dracut.conf.d/10-headless-edid.conf",
        "/etc/X11/xorg.conf.d/10-headless-nodpms.conf",
    ]:
        p = Path(path_str)
        if p.exists():
            backup_file(p)
            p.unlink()

    if shutil.which("update-initramfs"):
        subprocess.run(["update-initramfs", "-u"], check=False)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Linux Headless Kit CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("setup-monitor", help="Pasang Fake Monitor EDID di level Kernel DRM")
    sm.add_argument("--connector", help="Nama port DRM (misal HDMI-A-1, DP-1). Default: deteksi otomatis")
    sm.add_argument("--resolution", default="1920x1080", help="Resolusi fallback jika belum ada monitor terpasang (default: 1920x1080)")
    sm.add_argument("--edid-file", help="Path file binary EDID kustom (opsional)")
    sm.add_argument("--edid-name", default="headless-monitor.bin", help="Nama file tujuan di /lib/firmware/edid/")
    sm.add_argument("--force-generated-edid", action="store_true", help="Gunakan EDID generator bawaan walau monitor sedang terpasang")
    sm.add_argument("--autologin-user", help="User untuk autologin desktop X11")
    sm.add_argument("--no-autologin", action="store_true", help="Jangan ubah pengaturan autologin display manager")
    sm.set_defaults(func=cmd_setup_monitor)

    um = sub.add_parser("uninstall-monitor", help="Hapus konfigurasi Fake Monitor dari GRUB & initramfs")
    um.set_defaults(func=cmd_uninstall_monitor)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
