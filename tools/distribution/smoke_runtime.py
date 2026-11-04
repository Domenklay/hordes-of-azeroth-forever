#!/usr/bin/env python3
"""CI-only smoke test of the packaged engine. Never claims a game-client test."""
import argparse
import copy
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline
from distribution import runtime


def execute(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode:
        # CI annotations are visible even when the full job log needs sign-in.
        detail = (result.stdout + result.stderr)[-14000:]
        detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::error title=Offline runtime smoke::" + detail)
        raise RuntimeError("Packaged runtime command failed")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    for name in ("core.tar.gz", "mysql.tar.gz"):
        execute(["docker", "load", "--input", str(args.directory / name)])
    images = execute(["docker", "image", "ls", "--filter", "reference=hoa-forever-core:*",
                      "--format", "{{.Repository}}:{{.Tag}}"] ).splitlines()
    if len(images) != 1:
        raise ValueError("Expected exactly one packaged core image in this fresh CI runner")
    image = images[0]
    for binary in ("worldserver", "bnetserver"):
        version = execute(["docker", "run", "--rm", "--pull=never", "--network", "none",
                           "--entrypoint", f"/opt/trinitycore/bin/{binary}", image, "--version"])
        if "91d49b2" not in version:
            raise ValueError("Packaged core version does not match the pinned source")
        print(f"PASS: {binary} starts with the packaged shared libraries and pinned version")
    execute(["docker", "run", "--rm", "--pull=never", "--network", "none",
             "--entrypoint", "mysql", "mysql:8.0", "--version"])
    with tempfile.TemporaryDirectory(prefix="hoa-ci-smoke-") as temporary:
        root = Path(temporary)
        certs, data, state = root / "certs", root / "data", root / "profile"
        certs.mkdir()
        certificate_script = Path(__file__).with_name("certgen.sh").resolve()
        execute(["docker", "run", "--rm", "--pull=never", "--network", "none",
                 "--user", f"{os.getuid()}:{os.getgid()}",
                 "--mount", f"type=bind,source={certs},target=/certs",
                 "--mount", f"type=bind,source={certificate_script},target=/certgen.sh,readonly",
                 "--entrypoint", "bash", image, "/certgen.sh"])
        print("PASS: local certificate generation inside the actual packaged image")
        # These are fixtures, not game data. The TrinityCore worldserver
        # binary is NEVER started with them; explicit OpenSSL TCP listeners
        # below test only published ports and network migration.
        for relative in ("dbc/fixture", "maps/fixture", "vmaps/GameObjectModels.dtree"):
            path = data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("CI mount fixture, not game data", encoding="utf-8")
        offline.initialize(state, data, certs, 70532, image, "mysql:8.0")
        _, env, compose = offline.configuration(state)
        start = compose + ["up", "--detach", "--wait", "--wait-timeout", "180",
                           "--pull", "never", "--no-build", "mysql"]
        # SQL is sent over stdin. Credentials remain in the container's env;
        # they are never placed in process arguments or CI output.
        query = compose + ["exec", "-T", "mysql", "sh", "-c",
                           'MYSQL_PWD="$DB_PASSWORD" exec mysql -u trinity '
                           '--default-character-set=utf8mb4 --batch --raw --skip-column-names']
        probe = json.loads((offline.CONFIG / "compose.json").read_text(encoding="utf-8"))
        for service in ("worldserver", "bnetserver"):
            probe["services"][service]["init"] = True
            probe["services"][service]["stop_grace_period"] = "3s"
        probe["services"]["worldserver"].update(
            entrypoint=["openssl"], command=["s_server", "-nocert", "-www", "-accept", "0.0.0.0:8085"])
        probe["services"]["bnetserver"].update(
            entrypoint=["bash", "-c"],
            command=["openssl s_server -nocert -www -accept 0.0.0.0:1119 >/dev/null 2>&1 & "
                     "exec openssl s_server -nocert -www -accept 0.0.0.0:8081"])
        legacy = copy.deepcopy(probe)
        legacy["networks"]["local"]["internal"] = True
        def probe_command(config, name):
            path = root / name
            path.write_text(json.dumps(config), encoding="utf-8")
            command = list(compose)
            command[command.index("--file") + 1] = str(path)
            return command
        legacy_command = probe_command(legacy, "legacy-probe.json")
        bridge_command = probe_command(probe, "bridge-probe.json")
        up = ["up", "--detach", "--wait", "--wait-timeout", "180", "--pull", "never", "--no-build"]
        try:
            execute(legacy_command + up, env=env)
            if not offline.internal_network(compose):
                raise ValueError("Legacy network fixture was not internal")
            blocked = runtime.status(root)
            print("Legacy internal network host reachability:", blocked["ready"])
            count = execute(query, env=env, input=(
                "SELECT COUNT(*) FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME IN ('auth','characters','world','hotfixes');\n"))
            if count.strip() != "4":
                raise ValueError("The local database init script did not create all four databases")
            execute(query, env=env, input=(
                "CREATE TABLE characters.hoa_ci_persistence (id INT PRIMARY KEY, value VARCHAR(80));\n"
                "INSERT INTO characters.hoa_ci_persistence VALUES (1, 'Небесный эльф');\n"))
            offline.repair_network(env, compose, print)
            execute(bridge_command + up, env=env)
            accessible = runtime.status(root)
            if not accessible["ready"]:
                raise ValueError("Host ports were not published after migration: " + accessible["details"])
            if offline.internal_network(compose):
                raise ValueError("Network migration kept the internal flag")
            print("PASS: all three localhost ports and migration from the legacy internal network")
            execute(compose + ["stop", "mysql"], env=env)
            execute(start, env=env)
            text = execute(query, env=env, input="SELECT value FROM characters.hoa_ci_persistence WHERE id=1;\n")
            if text.strip() != "Небесный эльф":
                raise ValueError("Unicode data did not survive the database restart")
            print("PASS: schema initialization, application credentials and Cyrillic persistence after restart")
        except Exception:
            logs = subprocess.run(compose + ["logs", "--tail", "100", "mysql"], env=env,
                                  capture_output=True, text=True)
            detail = re.sub(r"\b[0-9a-f]{64}\b", "<generated-test-secret>", logs.stdout + logs.stderr)[-14000:]
            detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::error title=MySQL initialization log::" + detail)
            raise
        finally:
            # Only this test's freshly created, uniquely named CI project.
            # This cleanup is never exposed by the user-facing launcher.
            subprocess.run(compose + ["down", "--volumes", "--remove-orphans"], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Runtime smoke passed. No game client, character creation or Hordes run was tested.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"::error title=Offline runtime smoke::{error}")
        sys.exit(1)
