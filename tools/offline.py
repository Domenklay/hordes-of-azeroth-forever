#!/usr/bin/env python3
"""Local Forever runtime; never downloads images or deletes saved games."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "offline"


def check_files(data, certs):
    for name in ("dbc", "maps", "vmaps"):
        folder = data / name
        if not folder.is_dir() or not any(folder.iterdir()):
            raise ValueError(f"Нет извлечённых данных: {folder}")
    for path in (data / "vmaps/GameObjectModels.dtree",
                 certs / "forever.cert.pem", certs / "forever.key.pem",
                 certs / "ca/forever_root_ca.crt"):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Отсутствует или пуст файл: {path}")


def initialize(state, data, certs, build, image, mysql_image):
    data, certs = data.resolve(), certs.resolve()
    check_files(data, certs)
    # Refuse to replace credentials even if initialization was interrupted.
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    password = secrets.token_hex(32)
    for name, contents in {
        "database.env": f"DB_PASSWORD={password}\n",
        "mysql.env": f"DB_PASSWORD={password}\nMYSQL_ROOT_PASSWORD={secrets.token_hex(32)}\n",
    }.items():
        with open(os.open(state / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(contents)
    settings = {"data": str(data), "certs": str(certs), "build": build,
                "core_image": image, "mysql_image": mysql_image}
    (state / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    print(f"Локальный профиль сохранён: {state}. Пароли сгенерированы, игровые файлы не изменены.")


def configuration(state):
    if not (state / "settings.json").is_file():
        raise ValueError("Локальный профиль не создан. Сначала выполните init --data ... --certs ...")
    settings = json.loads((state / "settings.json").read_text())
    # Do not inherit arbitrary Compose overrides, profiles or a second env file.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "HOA_"))}
    env.update(HOA_STATE=str(state), HOA_CONFIG=str(CONFIG), HOA_DATA=settings["data"],
               HOA_CERTS=settings["certs"], HOA_CLIENT_BUILD=str(settings["build"]),
               HOA_CORE_IMAGE=settings["core_image"], HOA_MYSQL_IMAGE=settings["mysql_image"])
    project = "hoa-offline-" + hashlib.sha256(str(state).encode()).hexdigest()[:12]
    command = ["docker", "compose", "--project-name", project, "--env-file", os.devnull,
               "--file", str(CONFIG / "compose.json")]
    return settings, env, command


def require_images(settings):
    for image in dict.fromkeys((settings["core_image"], settings["mysql_image"])):
        result = subprocess.run(["docker", "image", "inspect", image],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise ValueError(f"Нет локального образа {image} или недоступен Docker. "
                             "Подготовьте Docker и образы заранее; автозагрузка отключена.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / ".offline",
                        help="постоянная папка профиля; не перемещать после создания")
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init", help="однократно создать локальный профиль")
    init.add_argument("--data", type=Path, required=True)
    init.add_argument("--certs", type=Path, required=True)
    init.add_argument("--build", type=int, choices=(70450, 70532), default=70450)
    init.add_argument("--image", default="forever-trinitycore:latest")
    init.add_argument("--mysql-image", default="mysql:8.0")
    for action in ("start", "stop", "status", "logs", "console", "validate"):
        sub.add_parser(action)
    args = parser.parse_args(argv)
    state = args.state.resolve()
    if args.action == "init":
        initialize(state, args.data, args.certs, args.build, args.image, args.mysql_image)
        return 0
    settings, env, command = configuration(state)
    operations = {
        "start": ["up", "--detach", "--pull", "never", "--no-build"],
        "stop": ["stop"], "status": ["ps", "--all"],
        "logs": ["logs", "--tail", "100", "worldserver", "bnetserver"],
        "console": ["attach", "worldserver"], "validate": ["config", "--quiet"],
    }
    if args.action == "start":
        check_files(Path(settings["data"]), Path(settings["certs"]))
        require_images(settings)
    result = subprocess.run(command + operations[args.action], env=env)
    if result.returncode == 0 and args.action == "start":
        print("Контейнеры запущены. Проверьте logs: первый импорт базы может занять много времени. "
              "До завершения импорта не останавливайте движок. Это не проверка готовности игры.")
    if result.returncode == 0 and args.action == "stop":
        print("Движок остановлен. База и персонажи сохранены в Docker volume.")
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        sys.exit(1)
