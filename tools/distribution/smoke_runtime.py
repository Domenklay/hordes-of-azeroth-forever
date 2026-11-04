#!/usr/bin/env python3
"""CI-only smoke test of the packaged engine. Never claims a game-client test."""
import argparse
import copy
import gzip
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
from distribution import survivor, realms


def test_survivor_sql(image, query, env, root, state):
    def embedded(path):
        return execute(["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", image,
                        "/src/TrinityCore/sql/" + path])
    schema = embedded("base/dev/world_database.sql")
    sql = "USE world;\n"
    for table in ("creature_template", "creature_template_model"):
        match = re.search(r"CREATE TABLE `" + table + r"` \(.*?;", schema, re.S)
        if not match:
            raise ValueError("Pinned world schema is missing " + table)
        sql += match.group() + "\n"
    sql += ("INSERT INTO creature_template (entry,name,faction) VALUES (6,'existing creature',7);\n"
            "INSERT INTO creature_template_model (CreatureID,Idx,CreatureDisplayID,DisplayScale,Probability,VerifiedBuild) "
            "VALUES (6,0,100,1,1,70532);\n")
    execute(query, env=env, input=sql)
    for _ in range(2):
        execute(query, env=env, input="USE characters;\n" + embedded("custom/characters/2026_11_04_00_hordes_characters.sql"))
        execute(query, env=env, input="USE world;\n" + embedded("custom/world/2026_11_04_00_hordes_world.sql"))
    data = execute(query, env=env, input=(
        "SELECT name,faction FROM world.creature_template WHERE entry=6;\n"
        "SELECT COUNT(*) FROM world.creature_template WHERE entry IN (900950,900951) AND ScriptName='hordes_forever_enemy';\n"
        "SELECT DisplayScale FROM world.creature_template_model WHERE CreatureID=900951;\n"))
    if data.strip().splitlines() != ["existing creature\t7", "2", "2"]:
        raise ValueError("Survivor migration changed a base entry or failed to create its dedicated entries: " + data)
    execute(query, env=env, input=("INSERT INTO characters.hoa_forever_progress (guid,runs,coins) VALUES (7,1,42);\n"))
    # Exercise the real mysqldump/atomic profile transition against this test's
    # isolated database, including restoration data for a saved progress row.
    saved = json.loads((state / "settings.json").read_text())
    saved["core_image"] = survivor.BASELINE
    (state / "settings.json").write_text(json.dumps(saved))
    survivor.upgrade(root, saved, {"modeProtocol": 1, "clientBuild": 70532}, image, print)
    backups = list((root / "backups").glob("*.sql.gz"))
    if len(backups) != 1 or "hoa_forever_progress" not in gzip.decompress(backups[0].read_bytes()).decode():
        raise ValueError("The real database backup is missing survivor progress")
    if json.loads((state / "settings.json").read_text())["core_image"] != image:
        raise ValueError("Profile did not switch to the verified engine after backup")
    print("PASS: actual pinned SQL schema, repeated additive migration and real account/character backup")
    auth = embedded("base/auth_database.sql")
    table = re.search(r"CREATE TABLE `realmlist` \(.*?;", auth, re.S).group()
    execute(query, env=env, input=("USE auth;\n" + table + "\n"
        "ALTER TABLE realmlist ADD COLUMN contentSetId INT UNSIGNED NOT NULL DEFAULT 137;\n"
        "INSERT INTO realmlist (id,name,port,icon,flag,gamebuild,contentSetId) VALUES "
        "(70,'Local Normal',8085,0,0,70532,137),(71,'Forever PvP',8095,1,0,70532,136),"
        "(72,'Forever RP',8105,6,0,70532,138),(73,'Forever Hardcore',8115,0,0,70532,140);\n"))
    _, realm_env, command = offline.configuration(state)
    if not realms.configure(root, command, realm_env, 70532, print):
        raise ValueError("The inherited catalog was not repaired")
    actual = execute(query, env=env, input=(
        "SELECT id,name,port,icon,contentSetId FROM auth.realmlist WHERE flag<>3 ORDER BY id;\n"
        "SELECT COUNT(*) FROM auth.realmlist;\n"
        "SELECT value FROM characters.hoa_ci_persistence WHERE id=1;\n"))
    if actual.strip().splitlines() != ["70\tHordes Offline\t8085\t0\t137", "4", "Небесный эльф"]:
        raise ValueError("Realm fix changed character data or exposed unserved endpoints: " + actual)
    if realms.configure(root, command, realm_env, 70532, print):
        raise ValueError("A repeated catalog repair was not idempotent")
    execute(query, env=env, input="UPDATE auth.realmlist SET flag=2 WHERE id=70;\n")
    try:
        realms.configure(root, command, realm_env, 70532, print)
    except ValueError:
        pass
    else:
        raise ValueError("Catalog repair incorrectly forced an offline world online")
    print("PASS: only Normal realm 70 is advertised, extra rows retained, characters unchanged and offline state respected")


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
    parser.add_argument("--survivor", action="store_true")
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
            command=["openssl s_server -cert /opt/trinitycore/certs/forever.cert.pem "
                     "-key /opt/trinitycore/certs/forever.key.pem -www -accept 0.0.0.0:1119 >/dev/null 2>&1 & "
                     "exec openssl s_server -cert /opt/trinitycore/certs/forever.cert.pem "
                     "-key /opt/trinitycore/certs/forever.key.pem -www -accept 0.0.0.0:8081"])
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
            realms.wait_tls(root)
            print("PASS: both local TLS ports verify against the generated CA and actual certificate hostname")
            execute(compose + ["stop", "mysql"], env=env)
            execute(start, env=env)
            text = execute(query, env=env, input="SELECT value FROM characters.hoa_ci_persistence WHERE id=1;\n")
            if text.strip() != "Небесный эльф":
                raise ValueError("Unicode data did not survive the database restart")
            print("PASS: schema initialization, application credentials and Cyrillic persistence after restart")
            if args.survivor:
                test_survivor_sql(image, query, env, root, state)
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
