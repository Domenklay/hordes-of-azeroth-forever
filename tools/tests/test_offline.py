"""Offline packaging checks; no client, database or game server is started."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("offline", Path(__file__).parents[1] / "offline.py")
offline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(offline)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = root / "profile"
        self.data, self.certs = root / "data with пробелы", root / "certs"
        for relative in ("dbc/ruRU/test.db2", "maps/test.map", "vmaps/GameObjectModels.dtree"):
            path = self.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture, not game data")
        for relative in ("forever.cert.pem", "forever.key.pem", "ca/forever_root_ca.crt"):
            path = self.certs / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture, not a certificate")

    def initialize(self):
        with contextlib.redirect_stdout(io.StringIO()):
            offline.initialize(self.state, self.data, self.certs, 70532, "local-core:test", "local-mysql:test")

    def test_generated_secrets_and_repeat_init_preserves_them(self):
        self.initialize()
        before = (self.state / "mysql.env").read_bytes()
        with self.assertRaises(FileExistsError):
            self.initialize()
        self.assertEqual(before, (self.state / "mysql.env").read_bytes())
        mysql = dict(line.split("=", 1) for line in before.decode().splitlines())
        db = (self.state / "database.env").read_text().strip().split("=", 1)[1]
        self.assertEqual(mysql["DB_PASSWORD"], db)
        self.assertNotEqual(mysql["MYSQL_ROOT_PASSWORD"], db)
        self.assertRegex(db, r"^[0-9a-f]{64}$")
        self.assertNotIn(db, (self.state / "settings.json").read_text())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((self.state / "mysql.env").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)

    def test_missing_data_creates_no_profile(self):
        (self.data / "vmaps/GameObjectModels.dtree").unlink()
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertFalse(self.state.exists())

    def test_missing_cert_is_rejected(self):
        (self.certs / "forever.key.pem").unlink()
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertFalse(self.state.exists())

    def test_stable_project_and_no_inherited_compose_overrides(self):
        self.initialize()
        with patch.dict(os.environ, {"COMPOSE_FILE": "unexpected.yml", "COMPOSE_PROFILES": "web", "HOA_DATA": "wrong"}):
            settings, env, command = offline.configuration(self.state)
        self.assertNotIn("COMPOSE_FILE", env)
        self.assertNotIn("COMPOSE_PROFILES", env)
        self.assertEqual(env["HOA_DATA"], str(self.data))
        self.assertEqual(settings["build"], 70532)
        self.assertEqual(command, offline.configuration(self.state)[2])
        self.assertIn(os.devnull, command)

    def test_start_never_builds_or_pulls(self):
        self.initialize()
        with patch.object(offline.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                result = offline.main(["--state", str(self.state), "start"])
        self.assertEqual(result, 0)
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(calls[0][:3], ["docker", "image", "inspect"])
        self.assertEqual(calls[1][:3], ["docker", "image", "inspect"])
        self.assertEqual(calls[-1][-5:], ["up", "--detach", "--pull", "never", "--no-build"])

    def test_no_images_does_not_start_containers(self):
        self.initialize()
        with patch.object(offline.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaises(ValueError):
                offline.main(["--state", str(self.state), "start"])
        self.assertEqual(run.call_count, 1)

    def test_stop_preserves_volumes_and_reports_failure(self):
        self.initialize()
        with patch.object(offline.subprocess, "run", return_value=subprocess.CompletedProcess([], 23)) as run:
            self.assertEqual(offline.main(["--state", str(self.state), "stop"]), 23)
        self.assertEqual(run.call_args.args[0][-1], "stop")
        self.assertNotIn("--volumes", run.call_args.args[0])

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose not installed")
    def test_real_compose_configuration(self):
        self.initialize()
        _, env, command = offline.configuration(self.state)
        # Capture privately: expanded config contains generated test passwords.
        result = subprocess.run(command + ["config", "--format", "json"], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "Docker Compose rejected the offline configuration")
        config = json.loads(result.stdout)
        self.assertEqual(set(config["services"]), {"mysql", "worldserver", "bnetserver"})
        self.assertTrue(config["networks"]["local"]["internal"])
        for service in config["services"].values():
            self.assertEqual(service["pull_policy"], "never")
            self.assertNotIn("build", service)
            self.assertEqual(service["restart"], "no")
            for port in service.get("ports", []):
                self.assertEqual(port["host_ip"], "127.0.0.1")
        world = config["services"]["worldserver"]
        self.assertEqual(world["environment"]["SOAP_ENABLED"], "0")
        self.assertEqual(world["environment"]["CLIENT_BUILD"], "70532")
        self.assertNotIn("MYSQL_ROOT_PASSWORD", world["environment"])
        data_mount = next(v for v in world["volumes"] if v["target"] == "/opt/trinitycore/data")
        self.assertEqual(data_mount["source"], str(self.data))
        self.assertTrue(data_mount["read_only"])
        # Compose omits false fields when normalizing; also inspect the source
        # so an accidentally removed explicit safeguard cannot pass this check.
        self.assertFalse(data_mount.get("bind", {}).get("create_host_path", False))
        source = json.loads((offline.CONFIG / "compose.json").read_text())
        for service in source["services"].values():
            for mount in service["volumes"]:
                if isinstance(mount, dict):
                    self.assertIs(mount["bind"]["create_host_path"], False)
        self.assertEqual(set(config["volumes"]), {"saves", "logs"})


if __name__ == "__main__":
    unittest.main()
