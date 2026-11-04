import gzip
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from distribution import survivor, inject_hordes


class SurvivorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "profile").mkdir()
        self.saved = {"build": 70532, "core_image": survivor.BASELINE, "mysql_image": "mysql:8.0"}
        (self.root / "profile/settings.json").write_text(json.dumps(self.saved))

    def test_addon_upgrade_preserves_previous_files_and_other_addons(self):
        source = self.root / "version/hordes-addon/HordesForever"
        shutil.copytree(Path(__file__).resolve().parents[2] / "mods/hordes-forever/addon/HordesForever", source)
        addons = self.root / "client/_classic_beta_/Interface/AddOns"
        (addons / "AnotherAddon").mkdir(parents=True)
        (addons / "AnotherAddon/a.lua").write_text("user addon")
        (addons / "HordesForever").mkdir()
        (addons / "HordesForever/a.lua").write_text("previous user file")
        survivor.install_addon(self.root / "version", self.root / "client")
        survivor.install_addon(self.root / "version", self.root / "client")
        backups = list(addons.glob("HordesForever.backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "a.lua").read_text(), "previous user file")
        self.assertEqual((addons / "AnotherAddon/a.lua").read_text(), "user addon")
        self.assertEqual((addons / "HordesForever/HordesForever.lua").read_bytes(), (source / "HordesForever.lua").read_bytes())

    def test_upgrade_cannot_interrupt_import(self):
        def execute(command, **_):
            if "ps" in command:
                return subprocess.CompletedProcess(command, 0, '[{"Service":"worldserver","State":"running"}]')
            return subprocess.CompletedProcess(command, 1, "")
        with patch.object(survivor.offline, "configuration", return_value=(self.saved, {}, ["docker", "compose"])), \
                patch.object(survivor.offline, "capture_command", side_effect=execute) as commands:
            with self.assertRaisesRegex(ValueError, "импорт не прерывался"):
                survivor.upgrade(self.root, self.saved, {"modeProtocol": 1, "clientBuild": 70532}, survivor.SURVIVOR, lambda _: None)
        self.assertFalse(any("stop" in call.args[0] for call in commands.call_args_list))

    def test_backup_failure_keeps_profile_version(self):
        class Dump:
            def __init__(self, *_, **__): self.stdout = io.BytesIO(b"partial")
            def wait(self): return 1
        with patch.object(survivor.offline, "configuration", return_value=(self.saved, {}, ["docker", "compose"])), \
                patch.object(survivor.offline, "capture_command", return_value=subprocess.CompletedProcess([], 0, "[]")), \
                patch.object(survivor.subprocess, "Popen", Dump):
            with self.assertRaisesRegex(ValueError, "Версия профиля не изменена"):
                survivor.upgrade(self.root, self.saved, {"modeProtocol": 1, "clientBuild": 70532}, survivor.SURVIVOR, lambda _: None)
        self.assertEqual(json.loads((self.root / "profile/settings.json").read_text()), self.saved)
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_injector_registers_exactly_once_and_keeps_existing_modules(self):
        loader = self.root / "src/server/scripts/Custom/custom_script_loader.cpp"
        loader.parent.mkdir(parents=True)
        loader.write_text("void AddSC_existing();\nvoid AddCustomScripts()\n{\n    AddSC_existing();\n}\n")
        module = Path(__file__).resolve().parents[2] / "mods/hordes-forever"
        inject_hordes.inject(self.root, module)
        inject_hordes.inject(self.root, module)
        result = loader.read_text()
        self.assertEqual(result.count("    AddSC_existing();"), 1)
        self.assertEqual(result.count("    AddSC_hordes_forever();"), 1)
        self.assertTrue((self.root / "sql/custom/characters/2026_11_04_00_hordes_characters.sql").is_file())


if __name__ == "__main__": unittest.main()
