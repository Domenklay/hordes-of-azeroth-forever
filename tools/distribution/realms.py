"""Expose only this installation's running Normal realm; preserve all rows."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import ssl
import time
import uuid

import offline

REALM_ID = 70
REALM_NAME = "Hordes Offline"
SELECT = """SELECT JSON_OBJECT('id',id,'name',name,'address',address,
 'localAddress',localAddress,'localSubnetMask',localSubnetMask,'port',port,
 'icon',icon,'flag',flag,'gamebuild',gamebuild,'contentSetId',contentSetId)
 FROM auth.realmlist ORDER BY id;"""


def mysql(command, env, sql):
    query = command + ["exec", "-T", "mysql", "sh", "-c",
                       'MYSQL_PWD="$DB_PASSWORD" exec mysql -u trinity '
                       '--default-character-set=utf8mb4 --batch --raw --skip-column-names']
    # Passwords stay in the container's environment, never arguments or logs.
    return offline.capture_command(query, env=env, input=sql).stdout


def desired(rows, build):
    primary = next((row for row in rows if row["id"] == REALM_ID), None)
    if primary is None:
        raise ValueError("В локальной базе отсутствует мир 70. Не выбирайте PvP/RP/Hardcore; проверьте журнал движка.")
    if primary["flag"] & 2:
        raise ValueError("Локальный PvE-мир ещё отмечен как offline. Дождитесь окончания запуска и повторите вход.")
    result = []
    for row in rows:
        row = dict(row)
        if row["id"] == REALM_ID:
            row.update(name=REALM_NAME, address="127.0.0.1", localAddress="127.0.0.1",
                       localSubnetMask="255.255.255.0", port=8085, icon=0,
                       contentSetId=137, gamebuild=build)
        else:
            # LOGIN_SEL_REALMLIST excludes flag=3 entirely. Do not delete any
            # realm or move characters between different realm IDs.
            row["flag"] = 3
        result.append(row)
    return result


def configure(root, command, env, build, progress):
    rows = [json.loads(line) for line in mysql(command, env, SELECT).splitlines() if line.strip()]
    target = desired(rows, build)
    if rows == target:
        return False
    directory = root / "backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    backup = directory / ("realmlist-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".json")
    with open(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as output:
        json.dump(rows, output, ensure_ascii=False, indent=2)
    progress("Настройка списка миров: только локальный PvE «Hordes Offline». Копия списка: " + str(backup))
    sql = f"""START TRANSACTION;
UPDATE auth.realmlist SET name='Hordes Offline',address='127.0.0.1',localAddress='127.0.0.1',
 localSubnetMask='255.255.255.0',port=8085,icon=0,contentSetId=137,gamebuild={int(build)} WHERE id=70;
UPDATE auth.realmlist SET flag=3 WHERE id<>70;
COMMIT;
"""
    mysql(command, env, sql)
    actual = [json.loads(line) for line in mysql(command, env, SELECT).splitlines() if line.strip()]
    if actual != target:
        raise ValueError("Список миров изменился во время настройки. Повторите вход после проверки журнала; копия сохранена.")
    return True


def wait_tls(root, timeout=20):
    # Check real TLS with this installation's CA and the certificate's DNS
    # name, including hostname verification. Never accept an arbitrary cert.
    ca = root / "certs/ca/forever_root_ca.crt"
    context = ssl.create_default_context(cafile=str(ca))
    deadline = time.monotonic() + timeout
    while True:
        try:
            for port in (1119, 8081):
                with socket.create_connection(("127.0.0.1", port), timeout=2) as raw:
                    with context.wrap_socket(raw, server_hostname="trinity.actual.battle.net"):
                        pass
            return
        except ssl.SSLCertVerificationError as error:
            raise ValueError("Сертификат локальной авторизации не прошёл проверку. Нажмите «Подготовить ПК» "
                             "в игровом лаунчере и проверьте журнал.") from error
        except OSError as error:
            if time.monotonic() >= deadline:
                raise ValueError("Авторизация ещё не готова после обновления списка миров. Повторите вход; "
                                 "если ошибка остаётся, скопируйте журнал движка.") from error
            time.sleep(0.5)


def ensure(root, build, progress=lambda _: None):
    _, env, command = offline.configuration(root / "profile")
    changed = configure(root, command, env, build, progress)
    # bnetserver otherwise caches the offline startup state for up to ten
    # seconds. Refresh once per world process BEFORE launching the client.
    identity = offline.capture_command(command + ["ps", "--quiet", "worldserver"], env=env).stdout.strip()
    if not identity or any(c not in "0123456789abcdef" for c in identity.lower()):
        raise ValueError("Не удалось определить запущенный контейнер игрового мира.")
    started = offline.capture_command(["docker", "inspect", "--format", "{{.State.StartedAt}}", identity]).stdout.strip()
    key = hashlib.sha256((identity + started + str(build)).encode()).hexdigest()
    stamp = root / "realm-sync.json"
    synchronized = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else ""
    if changed or synchronized != key:
        progress("Обновление авторизации после готовности PvE-мира…")
        offline.capture_command(command + ["restart", "--timeout", "30", "bnetserver"], env=env)
        wait_tls(root)
        temporary = stamp.with_suffix(".tmp")
        temporary.write_text(key + "\n", encoding="utf-8")
        os.replace(temporary, stamp)
    progress("Доступен локальный мир: Normal / Обычный → Hordes Offline. PvP, RP и Hardcore скрыты.")


def select_client_realm(root, client):
    # The native launcher reads this dedicated config first. Keep the user's
    # Config.wtf intact and avoid remembering the previously selected PvP realm.
    directory = client / "_classic_beta_/WTF"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "ForeverLauncher.wtf"
    source = target if target.exists() else directory / "Config.wtf"
    original = source.read_bytes() if source.is_file() else b""
    text = original.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if not re.match(r"\s*SET\s+realmName\s+", line, re.I)]
    lines.append('SET realmName "Hordes Offline"')
    content = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    if target.is_file() and original == content:
        return
    if target.is_file():
        backups = root / "backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        backup = backups / ("ForeverLauncher-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".wtf")
        with open(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(original)
    temporary = target.with_suffix(".hordes.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, target)
