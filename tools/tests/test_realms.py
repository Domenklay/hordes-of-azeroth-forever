import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from distribution import realms


def catalog(flag=0):
    return [dict(id=70, name="Hordes Offline", address="127.0.0.1", localAddress="127.0.0.1",
                 localSubnetMask="255.255.255.0", port=8085, icon=0, flag=flag, gamebuild=70532, contentSetId=137),
            dict(id=71, name="Forever PvP", address="127.0.0.1", localAddress="127.0.0.1",
                 localSubnetMask="255.255.255.0", port=8095, icon=1, flag=0, gamebuild=70532, contentSetId=136)]


class RealmTests(unittest.TestCase):
    def test_selected_realm_changes_only_launcher_config_and_preserves_previous_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = root / "client"
            wtf = client / "_classic_beta_/WTF"
            wtf.mkdir(parents=True)
            normal = b'SET graphicsQuality "4"\r\nSET realmName "Forever PvP"\r\n'
            previous = normal + b'SET textLocale "ruRU"\r\n'
            (wtf / "Config.wtf").write_bytes(normal)
            (wtf / "ForeverLauncher.wtf").write_bytes(previous)
            realms.select_client_realm(root, client)
            realms.select_client_realm(root, client)
            result = (wtf / "ForeverLauncher.wtf").read_text()
            self.assertIn('SET realmName "Hordes Offline"', result)
            self.assertIn('SET graphicsQuality "4"', result)
            self.assertIn('SET textLocale "ruRU"', result)
            self.assertEqual((wtf / "Config.wtf").read_bytes(), normal)
            backups = list((root / "backups").glob("*.wtf"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), previous)

    def test_catalog_keeps_ids_and_characters_and_hides_unserved_realms(self):
        original = catalog()
        fixed = realms.desired(original, 70532)
        self.assertEqual(fixed[0], original[0])
        self.assertEqual(fixed[1], dict(original[1], flag=3))
        self.assertEqual(original[1]["flag"], 0)
        self.assertEqual(realms.desired(fixed, 70532), fixed)

    def test_offline_primary_is_not_forced_online(self):
        with self.assertRaisesRegex(ValueError, "отмечен как offline"):
            realms.desired(catalog(flag=2), 70532)

    def test_catalog_backup_precedes_mutation_and_failure_preserves_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def execute(_, __, sql):
                if "UPDATE" in sql:
                    backup = list((root / "backups").glob("realmlist-*.json"))
                    self.assertEqual(json.loads(backup[0].read_text(encoding="utf-8")), catalog())
                    self.assertNotIn("DELETE", sql.upper())
                    raise RuntimeError("database unavailable")
                return "\n".join(json.dumps(row) for row in catalog())
            with patch.object(realms, "mysql", side_effect=execute):
                with self.assertRaises(RuntimeError): realms.configure(root, [], {}, 70532, lambda _: None)
            self.assertEqual(len(list((root / "backups").glob("*.json"))), 1)

    def test_auth_cache_refreshes_once_per_world_process_before_client_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def execute(command, **_):
                out = "2026-11-04T16:00:00Z" if "inspect" in command else "aabbccdd"
                return subprocess.CompletedProcess(command, 0, out)
            with patch.object(realms.offline, "configuration", return_value=({}, {}, ["docker", "compose"])), \
                    patch.object(realms, "configure", return_value=False), \
                    patch.object(realms.offline, "capture_command", side_effect=execute) as calls, \
                    patch.object(realms, "wait_tls") as tls:
                realms.ensure(root, 70532)
                realms.ensure(root, 70532)
                self.assertEqual(sum("restart" in c.args[0] for c in calls.call_args_list), 1)
                self.assertEqual(tls.call_count, 1)

    def test_failed_tls_never_marks_catalog_synchronized(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(realms.offline, "configuration", return_value=({}, {}, ["docker", "compose"])), \
                    patch.object(realms, "configure", return_value=True), \
                    patch.object(realms.offline, "capture_command", return_value=subprocess.CompletedProcess([], 0, "aabbcc")), \
                    patch.object(realms, "wait_tls", side_effect=ssl.SSLCertVerificationError("untrusted")):
                with self.assertRaises(ssl.SSLCertVerificationError): realms.ensure(root, 70532)
            self.assertFalse((root / "realm-sync.json").exists())
