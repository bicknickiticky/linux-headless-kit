# Linux Headless Kit

Paket *plug-and-play* untuk membuat PC Linux tetap berjalan penuh **tanpa monitor fisik (*headless*)** dan **otomatis terhubung ke Wi-Fi Open (tanpa password)** saat kabel LAN tidak tersambung — ideal untuk akses jarak jauh via **AnyDesk, RustDesk, Sunshine/Moonlight, VNC, atau Tailscale**.

---

## Fitur Utama

### 1. Fake Monitor (Kernel DRM EDID Injector)
Membuat GPU (AMD `amdgpu`, Intel `i915`/`xe`, NVIDIA/Nouveau) tetap menganggap ada monitor terpasang di level Kernel Linux — **tanpa alat HDMI Dummy Plug** dan **akselerasi hardware GPU tetap aktif 100%**.
- **Clone EDID Otomatis:** Jika saat instalasi ada monitor terpasang, skrip otomatis menyalin profil EDID asli monitor tersebut sehingga saat monitor dimatikan/dinyalakan tidak muncul layar kedua palsu (*ghost display*).
- **Generator EDID Bawaan (`1920x1080`, `2560x1080`, `2560x1440`, kustom):** Jika diinstal lewat SSH pada PC yang sedang tidak dicolok monitor sama sekali, skrip otomatis membuat binary EDID digital VESA 1.4 yang valid.
- **Bundling Initramfs Multi-Distro:** Mendukung `initramfs-tools` (Ubuntu/Debian/Mint/Pop!_OS), `mkinitcpio` (Arch/Manjaro/CachyOS), dan `dracut` (Fedora/RHEL/openSUSE).
- **Autologin X11 & Anti-Blank (DPMS):** Mengatur autologin (SDDM / GDM3 / LightDM) dan mematikan DPMS *screen blanking* agar tampilan GUI selalu siap diremote setelah *reboot* mati lampu.
- **Backup Otomatis:** Selalu menyimpan salinan `.backup.<timestamp>` sebelum mengubah `/etc/default/grub` atau konfigurasi Display Manager.

### 2. Auto Open Wi-Fi Fallback (Saat Tanpa LAN)
Memastikan PC headless tetap online otomatis begitu menemukan jaringan Wi-Fi terbuka (misal Hotspot HP tanpa password) ketika kabel LAN terputus.
- **Hanya Aktif Saat Tanpa LAN:** Jika Ethernet/LAN terhubung, PC tidak akan menyentuh Wi-Fi Open (dan otomatis memutus koneksi Wi-Fi Open darurat begitu kabel LAN dicolok kembali).
- **Mendukung Semua Adapter Wi-Fi Dinamis:** Bekerja otomatis dengan USB Wi-Fi (`wlx*`) maupun PCIe Wi-Fi (`wlp*`/`wlan*`) apa pun tanpa perlu *setting* ulang saat ganti colok adapter.
- **Prioritas Jaringan Tersimpan:** Tidak akan mengganggu koneksi Wi-Fi ber-password yang sudah tersimpan di `NetworkManager`.
- **Dual Trigger (Instan + Periodik):** Bereaksi seketika lewat `NetworkManager dispatcher` saat kabel LAN lepas, ditambah `systemd timer` tiap 30 detik untuk mendeteksi Hotspot Open yang baru dinyalakan belakangan.

---

## Cara Pakai di PC Linux Lain (Cukup 2 Perintah)

```bash
git clone https://github.com/bicknickiticky/linux-headless-kit.git
cd linux-headless-kit
sudo ./install.sh
```

Setelah selesai, matikan PC, cabut monitor / kabel LAN, lalu nyalakan kembali.

---

## Opsi Lanjutan

```bash
# Pasang hanya Fake Monitor saja:
sudo ./install.sh --only-monitor

# Pasang hanya Auto Open Wi-Fi saja:
sudo ./install.sh --only-wifi

# Paksa port & resolusi tertentu (misal PC diinstal via SSH tanpa monitor sama sekali):
sudo ./install.sh --connector HDMI-A-1 --resolution 1920x1080 --force-generated-edid

# Pasang tanpa mengubah pengaturan Autologin:
sudo ./install.sh --no-autologin
```

### Cek Log Aktivitas Auto Open Wi-Fi
```bash
journalctl -t auto-open-wifi -f
```

### Uninstall / Kembalikan ke Pengaturan Awal
```bash
sudo ./uninstall.sh
```

---

## Menjalankan Unit Test
```bash
python3 -m unittest discover -s tests -v
```
