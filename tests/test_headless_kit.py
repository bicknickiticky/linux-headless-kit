import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import headless_kit as hk


class TestEdidGenerator(unittest.TestCase):
    def test_1080p_edid_is_valid_128_byte_block(self):
        edid = hk.generate_edid(1920, 1080, 60)
        self.assertEqual(len(edid), 128)
        self.assertEqual(edid[:8], b"\x00\xff\xff\xff\xff\xff\xff\x00")
        self.assertEqual(sum(edid) % 256, 0)
        # Detailed Timing Descriptor starts at byte 54:
        # Horizontal active low byte = 1920 & 0xFF = 0x80, upper nibble in byte 58 = (1920 >> 8) << 4 = 0x70
        self.assertEqual(edid[56], 1920 & 0xFF)
        self.assertEqual((edid[58] >> 4) & 0x0F, (1920 >> 8) & 0x0F)
        self.assertEqual(edid[59], 1080 & 0xFF)
        self.assertEqual((edid[61] >> 4) & 0x0F, (1080 >> 8) & 0x0F)

    def test_ultrawide_2560x1080_edid_checksum(self):
        edid = hk.generate_edid(2560, 1080, 60)
        self.assertEqual(len(edid), 128)
        self.assertEqual(sum(edid) % 256, 0)


class TestConnectorSelection(unittest.TestCase):
    def test_prefers_connected_external_over_internal_edp(self):
        ports = [
            {"card_port": "card0-eDP-1", "connector": "eDP-1", "status": "connected", "edid_len": 128},
            {"card_port": "card0-HDMI-A-1", "connector": "HDMI-A-1", "status": "disconnected", "edid_len": 0},
            {"card_port": "card0-DP-1", "connector": "DP-1", "status": "connected", "edid_len": 256},
        ]
        chosen = hk.choose_connector(ports)
        self.assertEqual(chosen["connector"], "DP-1")

    def test_falls_back_to_hdmi_when_completely_headless(self):
        ports = [
            {"card_port": "card1-DP-1", "connector": "DP-1", "status": "disconnected", "edid_len": 0},
            {"card_port": "card1-HDMI-A-1", "connector": "HDMI-A-1", "status": "disconnected", "edid_len": 0},
        ]
        chosen = hk.choose_connector(ports)
        self.assertEqual(chosen["connector"], "HDMI-A-1")


class TestGrubCmdlineUpdater(unittest.TestCase):
    def test_injects_and_updates_idempotently_and_removes_cleanly(self):
        orig = 'GRUB_DEFAULT=0\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n'
        updated = hk.update_grub_content(orig, "HDMI-A-1", "edid/headless-monitor.bin")
        self.assertIn(
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash drm.edid_firmware=HDMI-A-1:edid/headless-monitor.bin video=HDMI-A-1:e"',
            updated,
        )
        # Re-running with different port replaces instead of duplicating
        updated2 = hk.update_grub_content(updated, "DP-1", "edid/headless-monitor.bin")
        self.assertEqual(updated2.count("drm.edid_firmware="), 1)
        self.assertIn("drm.edid_firmware=DP-1:edid/headless-monitor.bin video=DP-1:e", updated2)

        # Removing restores original clean cmdline
        cleaned = hk.remove_grub_params(updated2)
        self.assertEqual(cleaned, orig)


class TestAutoOpenWifiLogic(unittest.TestCase):
    def test_nmcli_escaped_parser_and_open_ranking(self):
        lines = [
            r" :Office\:WPA:11\:22\:33\:44\:55\:01:WPA2:99",
            r" :Cafe\:Free:11\:22\:33\:44\:55\:02:--:55",
            r" :PhoneHotspot:11\:22\:33\:44\:55\:03::91",
            r" :ModernOWE:11\:22\:33\:44\:55\:04:OWE:73",
            r" ::11\:22\:33\:44\:55\:05::100",
        ]
        ranked = hk.rank_open_networks(lines)
        self.assertEqual(ranked, [("PhoneHotspot", 91), ("ModernOWE", 73), ("Cafe:Free", 55)])

    def test_malicious_ssid_profile_sanitization(self):
        prof = hk.make_profile_name("FreeWiFi;$(evil_cmd)`id`")
        self.assertTrue(prof.startswith("auto-open-FreeWiFi_evil_cmd_"))
        self.assertRegex(prof, r"^auto-open-[A-Za-z0-9_-]+$")

    def test_prefers_plasmax11_over_plasma_for_kde6_compatibility(self):
        chosen = hk.pick_x11_session(["plasma.desktop", "plasmax11.desktop", "fluxbox.desktop"])
        self.assertEqual(chosen, "plasmax11.desktop")


if __name__ == "__main__":
    unittest.main()
