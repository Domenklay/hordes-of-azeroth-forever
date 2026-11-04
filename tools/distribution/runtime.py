"""Prepare the local engine and hand game login to the native Forever launcher."""
import json
import os
from pathlib import Path
import socket
import subprocess

import offline
from .installer import validate_client

SERVICES = {"mysql": "База", "worldserver": "Игровой мир", "bnetserver": "Авторизация"}


def command_output(command, **kwargs):
    return offline.capture_command(command, **kwargs).stdout


def parse_services(text):
    return offline.parse_services(text)


def status(root):
    _, env, command = offline.configuration(root / "profile")
    services = parse_services(command_output(command + ["ps", "--all", "--format", "json"], env=env))
    lines, failed = [], False
    all_running = True
    translations = {"running": "работает", "exited": "остановлен", "dead": "аварийно остановлен",
                    "created": "создан, ещё не запущен", "restarting": "перезапускается", "paused": "приостановлен"}
    for name, label in SERVICES.items():
        service = services.get(name, {})
        state = str(service.get("State", "missing")).lower()
        all_running = all_running and state == "running"
        line = f"{label}: {translations.get(state, state if service else 'не запущен')}"
        if state in ("exited", "dead"):
            code = service.get("ExitCode")
            if code is not None:
                line += f" (код {code})"
            failed = failed or state == "dead" or str(code or 0) != "0"
        if service.get("Health") == "unhealthy":
            line += "; проверка работоспособности не пройдена"
            failed = True
        lines.append(line)
    ready = all_running and not failed and services.get("mysql", {}).get("Health", "") in ("", "healthy")
    if ready:
        for port, label in ((1119, "Вход"), (8081, "Вход по HTTPS"), (8085, "Игровой мир")):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
                lines.append(f"{label}, порт {port}: доступен")
            except OSError:
                ready = False
                lines.append(f"{label}, порт {port}: нет подключения")
    if failed:
        message = "Один из процессов завершился с ошибкой. Откройте «Журнал движка»; ожидание импорта это не исправит."
    elif ready:
        message = "Движок принимает подключения. Можно открыть игровой лаунчер."
    elif all_running and offline.internal_network(command):
        message = ("Обнаружена устаревшая сеть Docker, блокирующая подключение с компьютера. "
                   "Нажмите «Запустить локальный движок» для исправления с сохранением базы.")
    elif all_running:
        message = ("Процессы работают, но подключения ещё недоступны. Может идти подготовка или импорт; "
                   "причину и продвижение смотрите в «Журнале движка».")
    else:
        message = "Движок не запущен полностью. Проверьте состояния процессов и «Журнал движка»."
    return {"ready": ready, "failed": failed, "message": message, "details": "\n".join(lines)}


def require_ready(root):
    report = status(root)
    if not report["ready"]:
        raise ValueError(report["message"] + "\n" + report["details"])


def prepare(root, version, manifest, client, progress):
    validate_client(client, manifest["clientBuild"])
    images = {p["id"]: p.get("image") for p in manifest["packages"]}
    settings = {"core_image": images["core"], "mysql_image": images["mysql"]}
    offline.require_images(settings)
    profile = root / "profile"
    if profile.exists():
        saved, _, _ = offline.configuration(profile)
        if saved["build"] != manifest["clientBuild"] or saved["core_image"] != images["core"]:
            raise ValueError("Существующая база относится к другому движку/сборке. "
                             "Автообновление базы отключено: нужна отдельная миграция и резервная копия.")
        offline.check_files(Path(saved["data"]), Path(saved["certs"]))
        return profile
    certs = root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("certgen.sh").resolve()
    progress("Создание локальных сертификатов…")
    command_output(["docker", "run", "--rm", "--pull=never", "--network", "none",
                    "--mount", f"type=bind,source={certs},target=/certs",
                    "--mount", f"type=bind,source={script},target=/certgen.sh,readonly",
                    "--entrypoint", "bash", images["core"], "/certgen.sh"])
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    try:
        offline.check_files(data, certs)
    except ValueError:
        # Extraction may be resumed after failure, but only inside the app's
        # dedicated output folder. The user's client is mounted read-only.
        progress("Извлечение данных клиента. Это может занять много времени…")
        command_output(["docker", "run", "--rm", "--pull=never", "--network", "none",
                        "--mount", f"type=bind,source={client},target=/wow,readonly",
                        "--mount", f"type=bind,source={data},target=/opt/trinitycore/data",
                        "--entrypoint", "/usr/local/bin/extract.sh", images["core"]])
    offline.initialize(profile, data, certs, manifest["clientBuild"], images["core"], images["mysql"])
    return profile


def engine(root, operation, progress=None):
    profile = root / "profile"
    settings, env, command = offline.configuration(profile)
    if operation == "start":
        offline.check_files(Path(settings["data"]), Path(settings["certs"]))
        offline.require_images(settings)
        offline.repair_network(env, command, progress)
    args = {"start": ["up", "--detach", "--pull", "never", "--no-build"],
            "stop": ["stop"], "logs": ["logs", "--tail", "60", "mysql", "worldserver", "bnetserver"]}[operation]
    return command_output(command + args, env=env)


def console(root):
    require_ready(root)
    _, env, command = offline.configuration(root / "profile")
    return subprocess.Popen(command + ["attach", "--sig-proxy=false", "worldserver"], env=env,
                            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def launch(root, version, manifest, client):
    validate_client(client, manifest["clientBuild"])
    require_ready(root)
    native = version / "game-launcher" / "forever-launcher.exe"
    if not native.is_file():
        raise ValueError("Не установлен игровой лаунчер")
    native_config = root / "game-settings"
    native_config.mkdir(parents=True, exist_ok=True)
    settings_file = native_config / "settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else {}
    settings.update(gameDir=str(client / "_classic_beta_"), locale="ruRU", language="ru",
                    server={"name": "Hordes Offline", "portal": "trinity.actual.battle.net",
                            "address": "127.0.0.1", "bnetPort": 1119, "restPort": 8081,
                            "worldPort": 8085, "regionGroup": 8, "website": "",
                            "publicKey": "02596F0D0C061A8B30745988FD72C59E29EC367FB0F341F28E0F08D037BAFC69"})
    settings_file.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    env = dict(os.environ, HOA_NATIVE_CONFIG=str(native_config),
               HOA_LOCAL_CA=str(root / "certs/ca/forever_root_ca.crt"))
    return subprocess.Popen([str(native)], cwd=native.parent, env=env)
