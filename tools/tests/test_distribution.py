import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from distribution import installer, runtime, patch_native


def part(payload=b"test"):
    return {"url": f"https://github.com/{installer.REPOSITORY}/releases/download/offline-v1/test.part",
            "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def manifest():
    return {"schema": 1, "version": "1.0-test", "clientBuild": 70532, "packages": [
        {"id": "core", "kind": "docker-image", "image": "core:test", "parts": [part()]},
        {"id": "mysql", "kind": "docker-image", "image": "mysql:8.0", "parts": [part()]},
        {"id": "game-launcher", "kind": "native-launcher", "parts": [part()]},
    ]}


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_only_our_github_release_urls(self):
        for url in ("http://github.com/Domenklay/hordes-of-azeroth-forever/releases/download/v1/a",
                    "https://github.com.attacker.test/Domenklay/hordes-of-azeroth-forever/releases/download/v1/a",
                    "https://github.com/other/repository/releases/download/v1/a",
                    "https://github.com/Domenklay/hordes-of-azeroth-forever/releases/download/v1/a?x=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                installer.release_url(url)

    def test_manifest_rejects_incomplete_or_ambiguous_packages(self):
        invalid = []
        a = manifest(); a["clientBuild"] = 5875; invalid.append(a)
        a = manifest(); a["packages"].pop(); invalid.append(a)
        a = manifest(); a["packages"][0]["parts"][0]["sha256"] = ""; invalid.append(a)
        a = manifest(); a["packages"][0]["kind"] = "client"; invalid.append(a)
        a = manifest(); a["packages"][0]["id"] = "../state"; invalid.append(a)
        a = manifest(); a["packages"][0]["parts"][0]["size"] = True; invalid.append(a)
        a = manifest(); a["packages"].append(copy.deepcopy(a["packages"][0])); invalid.append(a)
        for data in invalid:
            with self.assertRaises(ValueError):
                installer.validate_manifest(data)
        self.assertEqual(installer.validate_manifest(manifest())["clientBuild"], 70532)

    def test_download_hash_mismatch_leaves_no_usable_file(self):
        with patch.object(installer.urllib.request, "urlopen", return_value=io.BytesIO(b"fail")):
            with self.assertRaises(ValueError):
                installer.download(part(), self.root, lambda _: None)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_download_size_limit_and_cache_reverification(self):
        spec = part(b"okay")
        with patch.object(installer.urllib.request, "urlopen", return_value=io.BytesIO(b"okay")) as request:
            path = installer.download(spec, self.root, lambda _: None)
            self.assertEqual(path.read_bytes(), b"okay")
            installer.download(spec, self.root, lambda _: None)
            self.assertEqual(request.call_count, 1)
        path.write_bytes(b"evil")
        with patch.object(installer.urllib.request, "urlopen", return_value=io.BytesIO(b"too much")):
            with self.assertRaises(ValueError):
                installer.download(spec, self.root, lambda _: None)

    def test_archives_cannot_escape_or_create_links(self):
        for name in ("../outside", "/outside", "C:/outside", "a\\..\\outside", "a./file"):
            archive = self.root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(name, "bad")
            with self.assertRaises(ValueError):
                installer.unpack_zip(archive, self.root)
        with zipfile.ZipFile(archive, "w") as z:
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            z.writestr(link, "../outside")
        with self.assertRaises(ValueError):
            installer.unpack_zip(archive, self.root)
        self.assertFalse((self.root.parent / "outside").exists())

    def test_unavailable_client_is_not_silently_skipped(self):
        with self.assertRaisesRegex(ValueError, "нет архива клиента"):
            installer.install(manifest(), self.root, True)

    def test_client_requires_exact_active_forever_build(self):
        (self.root / "Data").mkdir()
        (self.root / "_classic_beta_").mkdir()
        (self.root / "_classic_beta_/WowB.exe").write_bytes(b"test")
        (self.root / ".build.info").write_text(
            "Product!STRING:0|Active!DEC:1|Version!STRING:0\n"
            "wow_classic_beta|1|1.60.1.70532\n")
        installer.validate_client(self.root, 70532)
        with self.assertRaises(ValueError):
            installer.validate_client(self.root, 70450)

    def test_failed_install_does_not_activate_or_overlay_previous_version(self):
        old = self.root / "packages/old"
        old.mkdir(parents=True)
        (old / "save").write_text("keep")
        current = self.root / "current.json"
        current.write_text("old")
        with patch.object(installer, "download", side_effect=OSError("connection lost")):
            with self.assertRaises(OSError):
                installer.install(manifest(), self.root, False, lambda _: None)
        self.assertEqual(current.read_text(), "old")
        self.assertEqual((old / "save").read_text(), "keep")
        self.assertFalse((self.root / "packages/1.0-test").exists())

    def test_split_assets_reassemble_exactly(self):
        spec = importlib.util.spec_from_file_location("make_release", TOOLS / "distribution/make_release.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"installer": installer}):
            spec.loader.exec_module(module)
        data = bytes(range(233)) * 4
        source = self.root / "source"
        source.write_bytes(data)
        result = module.package(source, "core", "docker-image", self.root, "offline-v1", "core:1", part_size=137)
        blocks = []
        for index, metadata in enumerate(result["parts"]):
            block = (self.root / f"core.{index:03d}.part").read_bytes()
            self.assertEqual(len(block), metadata["size"])
            self.assertEqual(hashlib.sha256(block).hexdigest(), metadata["sha256"])
            blocks.append(block)
        self.assertEqual(b"".join(blocks), data)

    def test_native_launch_uses_isolated_settings_and_local_public_ca(self):
        client = self.root / "client"
        version = self.root / "packages/test"
        (version / "game-launcher").mkdir(parents=True)
        (version / "game-launcher/forever-launcher.exe").write_bytes(b"test")
        with patch.object(runtime, "validate_client"), patch.object(runtime, "status", return_value={"ready": True}), \
                patch.object(runtime.subprocess, "Popen") as spawn:
            runtime.launch(self.root, version, manifest(), client)
        kwargs = spawn.call_args.kwargs
        self.assertEqual(kwargs["env"]["HOA_LOCAL_CA"], str(self.root / "certs/ca/forever_root_ca.crt"))
        self.assertEqual(kwargs["env"]["HOA_NATIVE_CONFIG"], str(self.root / "game-settings"))
        settings = json.loads((self.root / "game-settings/settings.json").read_text())
        self.assertEqual(settings["server"]["address"], "127.0.0.1")
        self.assertEqual(settings["locale"], "ruRU")

    def test_compose_status_formats(self):
        values = [{"Service": "mysql", "State": "running"}, {"Service": "worldserver", "State": "exited"}]
        expected = {value["Service"]: value for value in values}
        self.assertEqual(runtime.parse_services(json.dumps(values)), expected)
        self.assertEqual(runtime.parse_services("\n".join(json.dumps(v) for v in values)), expected)
        self.assertEqual(runtime.parse_services(""), {})
        with self.assertRaises(ValueError):
            runtime.parse_services('{"unexpected":true}')

    def report(self, world_state="running", exit_code=0, mysql_health="healthy", connection_error=False):
        values = [{"Service": "mysql", "State": "running", "Health": mysql_health},
                  {"Service": "worldserver", "State": world_state, "ExitCode": exit_code},
                  {"Service": "bnetserver", "State": "running"}]
        with patch.object(runtime.offline, "configuration", return_value=({}, {}, ["docker", "compose"])), \
                patch.object(runtime.offline, "internal_network", return_value=False), \
                patch.object(runtime, "command_output", return_value=json.dumps(values)), \
                patch.object(runtime.socket, "create_connection") as connect:
            if connection_error:
                connect.side_effect = ConnectionRefusedError()
            return runtime.status(self.root)

    def test_crashed_world_is_reported_as_failure_not_import(self):
        report = self.report(world_state="exited", exit_code=1)
        self.assertFalse(report["ready"])
        self.assertTrue(report["failed"])
        self.assertIn("код 1", report["details"])
        self.assertIn("завершился с ошибкой", report["message"])

    def test_running_processes_without_connections_are_not_ready(self):
        report = self.report(connection_error=True)
        self.assertFalse(report["ready"])
        self.assertFalse(report["failed"])
        self.assertIn("подготовка или импорт", report["message"])
        for port in (1119, 8081, 8085):
            self.assertIn(str(port), report["details"])
        self.assertTrue(self.report()["ready"])
        self.assertFalse(self.report(mysql_health="starting")["ready"])
        self.assertTrue(self.report(mysql_health="unhealthy")["failed"])

    def test_console_is_not_opened_for_unready_engine(self):
        with patch.object(runtime, "status", return_value=self.report(world_state="exited", exit_code=1)), \
                patch.object(runtime.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "завершился с ошибкой"):
                runtime.console(self.root)
        spawn.assert_not_called()

    def test_prepare_rejects_implicit_database_upgrade(self):
        (self.root / "profile").mkdir()
        saved = {"build": 70450, "core_image": "old:1"}
        with patch.object(runtime, "validate_client"), patch.object(runtime.offline, "require_images"), \
                patch.object(runtime.offline, "configuration", return_value=(saved, {}, [])):
            with self.assertRaisesRegex(ValueError, "нет проверенного перехода"):
                runtime.prepare(self.root, self.root, manifest(), self.root, lambda _: None)

    def test_native_patch_refuses_unrecognized_source(self):
        source = self.root / "app/src-tauri/src"
        source.mkdir(parents=True)
        (source / "config.rs").write_text("changed upstream")
        with self.assertRaisesRegex(ValueError, "Pinned source changed"):
            patch_native.patch(self.root)

    @unittest.skipUnless(shutil.which("openssl") and shutil.which("bash"), "OpenSSL and bash required")
    def test_certificate_generation_preserves_existing_keys(self):
        certs = self.root / "certs"
        certs.mkdir()
        bash = shutil.which("bash")
        if os.name == "nt":
            git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
            if git_bash.is_file():
                bash = str(git_bash)
        command = [bash, (TOOLS / "distribution/certgen.sh").as_posix()]
        env = dict(os.environ, HOA_CERTS_DIR=certs.as_posix(), MSYS_NO_PATHCONV="1")
        result = subprocess.run(command, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        key = (certs / "ca/forever_root_ca.key").read_bytes()
        result = subprocess.run(command, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual((certs / "ca/forever_root_ca.key").read_bytes(), key)
        subprocess.run(["openssl", "verify", "-verify_hostname", "trinity.actual.battle.net",
                        "-CAfile", str(certs / "ca/forever_root_ca.crt"), str(certs / "forever.cert.pem")],
                       check=True, capture_output=True)
        (certs / "forever.cert.pem").unlink()
        result = subprocess.run(command, env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((certs / "ca/forever_root_ca.key").read_bytes(), key)

    def test_successful_install_is_atomic_and_repeatable(self):
        data = manifest()
        archive = self.root / "native.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("forever-launcher.exe", b"launcher fixture")
        def cached(spec, cache, progress):
            return archive
        with patch.object(installer, "download", side_effect=cached) as download, \
                patch.object(installer.subprocess, "run"):
            version = installer.install(data, self.root, False, lambda _: None)
            self.assertEqual((version / "game-launcher/forever-launcher.exe").read_bytes(), b"launcher fixture")
            self.assertEqual(json.loads((version / "installed.json").read_text()), data)
            self.assertEqual(installer.install(data, self.root, False, lambda _: None), version)
            self.assertEqual(download.call_count, 3)

    def test_patch_matches_pinned_native_source(self):
        donor = Path(os.environ.get("FOREVER_TEST_SOURCE", "/workspace/WoWForEver-reference"))
        if not donor.is_dir():
            self.skipTest("Pinned WoWForEver source checkout unavailable")
        paths = ["app/src-tauri/src/" + name + ".rs" for name in ("config", "api", "system", "main", "i18n")]
        paths += ["app/ui/i18n.js", "app/src-tauri/tauri.conf.json"]
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(donor / relative, target)
        patch_native.patch(self.root)
        self.assertIn("HOA_LOCAL_CA", (self.root / "app/src-tauri/src/main.rs").read_text())
        if shutil.which("node"):
            ui = self.root / "app/ui/i18n.js"
            code = ui.read_text() + "\nif(resolveLanguage('ruRU')!=='ru')throw Error('ru');lang='ru';if(t('play')!=='Играть')throw Error('play');"
            subprocess.run(["node", "-e", code], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
