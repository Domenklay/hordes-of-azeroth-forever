"""Upgrade the known local baseline and install only our dedicated addon."""
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

import offline

BASELINE = "hoa-forever-core:0.1.0-preview.2"
PREVIOUS_SURVIVOR = "hoa-forever-core:0.2.0-preview.1"
SURVIVOR = "hoa-forever-core:0.2.0-preview.3"


def upgrade(root, saved, manifest, image, progress):
    if (manifest.get("modeProtocol") != 1 or saved["build"] != 70532 or
            manifest["clientBuild"] != 70532 or saved["core_image"] not in (BASELINE, PREVIOUS_SURVIVOR) or image != SURVIVOR):
        raise ValueError("Для этой версии движка нет проверенного перехода с сохранением базы.")
    profile = root / "profile"
    _, env, command = offline.configuration(profile)
    services = offline.parse_services(offline.capture_command(
        command + ["ps", "--all", "--format", "json"], env=env).stdout)
    if services.get("worldserver", {}).get("State") == "running":
        probe = offline.capture_command(command + ["exec", "-T", "worldserver", "bash", "-c",
                                        "exec 3<>/dev/tcp/127.0.0.1/8085"], env=env, check=False)
        if probe.returncode:
            raise ValueError("Мир ещё загружается. Переход к режиму отложен: первый импорт не прерывался.")
    progress("Переход к вампирлайку: остановка мира и локальная резервная копия аккаунтов и персонажей…")
    offline.capture_command(command + ["stop", "--timeout", "60", "worldserver", "bnetserver"], env=env)
    offline.capture_command(command + ["up", "--detach", "--wait", "--wait-timeout", "180",
                                      "--pull", "never", "--no-build", "mysql"], env=env)
    directory = root / "backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    backup = directory / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".sql.gz")
    temporary = backup.with_suffix(".partial")
    query = command + ["exec", "-T", "mysql", "sh", "-c",
                       'MYSQL_PWD="$DB_PASSWORD" exec mysqldump -u trinity '
                       '--single-transaction --no-tablespaces --skip-lock-tables '
                       '--set-gtid-purged=OFF --databases auth characters']
    try:
        # Keep secrets in the container environment and backup, never in logs.
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            process = subprocess.Popen(query, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with gzip.GzipFile(fileobj=output, mode="wb") as compressed:
                shutil.copyfileobj(process.stdout, compressed)
            process.stdout.close()
            if process.wait():
                raise ValueError("Не удалось создать резервную копию. Версия профиля не изменена; проверьте базу.")
        if temporary.stat().st_size < 100:
            raise ValueError("Резервная копия пуста. Версия профиля не изменена.")
        os.replace(temporary, backup)
        settings = dict(saved, core_image=image)
        staged = profile / "settings.mode-upgrade.json"
        staged.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staged, profile / "settings.json")
        progress("Резервная копия сохранена: " + str(backup))
    finally:
        temporary.unlink(missing_ok=True)


def install_addon(version, client):
    source = version / "hordes-addon/HordesForever"
    if not (source / "HordesForever.toc").is_file() or not (source / "HordesForever.lua").is_file():
        raise ValueError("Сначала установите пакет с интерфейсом вампирлайка Hordes.")
    if os.name == "nt":
        processes = offline.capture_command(["tasklist", "/FI", "IMAGENAME eq WowB.exe", "/FO", "CSV", "/NH"])
        if '"wowb.exe"' in processes.stdout.lower():
            raise ValueError("Закройте игру и снова нажмите «Войти в игру»: требуется обновить аддон Hordes.")
    addons = client / "_classic_beta_/Interface/AddOns"
    addons.mkdir(parents=True, exist_ok=True)
    target = addons / "HordesForever"
    files = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    if target.is_dir() and {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()} == files:
        return
    with tempfile.TemporaryDirectory(prefix=".hordes-install-", dir=addons) as temporary:
        stage = Path(temporary) / "HordesForever"
        shutil.copytree(source, stage)
        backup = None
        if target.exists():
            backup = addons / ("HordesForever.backup." + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
            os.rename(target, backup)
        try:
            os.rename(stage, target)
        except Exception:
            if backup is not None:
                os.rename(backup, target)
            raise
