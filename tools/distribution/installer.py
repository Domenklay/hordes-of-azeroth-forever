"""Release installer shared by the Russian desktop launcher and its tests."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "Domenklay/hordes-of-azeroth-forever"
MANIFEST_URL = f"https://github.com/{REPOSITORY}/releases/latest/download/offline-manifest.json"
MAX_MANIFEST = 1024 * 1024


def release_url(url):
    parsed = urllib.parse.urlsplit(url)
    prefix = f"/{REPOSITORY}/releases/download/"
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or
            not parsed.path.startswith(prefix) or parsed.query or parsed.fragment):
        raise ValueError("Пакеты должны находиться в GitHub Releases нашего репозитория")
    return url


def validate_manifest(manifest):
    if manifest.get("schema") != 1 or manifest.get("clientBuild") not in (70450, 70532):
        raise ValueError("Неизвестный формат пакета или версия клиента")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", manifest.get("version", "")):
        raise ValueError("Некорректная версия пакета")
    packages = manifest.get("packages", [])
    ids = set()
    for package in packages:
        identity = package.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,50}", identity) or identity in ids:
            raise ValueError("Повторяющийся или некорректный идентификатор пакета")
        ids.add(identity)
        if package.get("kind") not in ("docker-image", "native-launcher", "client"):
            raise ValueError("Неизвестный тип пакета")
        if package["kind"] == "docker-image" and not re.fullmatch(r"[a-z0-9./:_-]+", package.get("image", "")):
            raise ValueError("Некорректное имя Docker-образа")
        if not package.get("parts"):
            raise ValueError("Пустой пакет")
        for part in package["parts"]:
            release_url(part["url"])
            if not re.fullmatch(r"[0-9a-f]{64}", part.get("sha256", "")):
                raise ValueError("У пакета отсутствует SHA-256")
            if type(part.get("size")) is not int or part["size"] <= 0:
                raise ValueError("Неизвестный размер пакета")
    if not {"core", "mysql", "game-launcher"}.issubset(ids):
        raise ValueError("Релиз не содержит движок, базу или игровой лаунчер")
    expected = {"core": "docker-image", "mysql": "docker-image", "game-launcher": "native-launcher"}
    for package in packages:
        if package["id"] in expected and package["kind"] != expected[package["id"]]:
            raise ValueError("Тип обязательного пакета не соответствует его назначению")
    return manifest


def fetch_manifest():
    request = urllib.request.Request(MANIFEST_URL, headers={"User-Agent": "HoA-Offline-Launcher/0.1"})
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = response.read(MAX_MANIFEST + 1)
    if len(payload) > MAX_MANIFEST:
        raise ValueError("Манифест слишком велик")
    return validate_manifest(json.loads(payload))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(part, cache, progress):
    """A final cache entry exists only after size and hash verification."""
    release_url(part["url"])
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / part["sha256"]
    if target.is_file() and target.stat().st_size == part["size"] and digest(target) == part["sha256"]:
        progress("Используется проверенный файл из кэша")
        return target
    temporary = target.with_suffix(".part")
    try:
        request = urllib.request.Request(part["url"], headers={"User-Agent": "HoA-Offline-Launcher/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            size, h = 0, hashlib.sha256()
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > part["size"]:
                    raise ValueError("Размер загрузки превышает указанный в релизе")
                output.write(block)
                h.update(block)
                progress(f"Загружено {size // (1024 * 1024)} / {part['size'] // (1024 * 1024)} МБ")
        if size != part["size"] or h.hexdigest() != part["sha256"]:
            raise ValueError("Проверка SHA-256/размера не пройдена; файл не установлен")
        os.replace(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def unpack_zip(archive, destination):
    """Extract only regular files into a fresh staging directory."""
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        required = sum(item.file_size for item in members)
        if required > shutil.disk_usage(destination).free:
            raise ValueError("Недостаточно свободного места для распаковки")
        seen = set()
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            mode = member.external_attr >> 16
            if (not name or "\\" in name or ":" in name or path.is_absolute() or
                    any(p in ("..", ".") or p.rstrip(" .") != p for p in path.parts) or
                    stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                raise ValueError("Небезопасный путь в архиве")
            normalized = str(path).casefold()
            if normalized in seen:
                raise ValueError("Повторяющийся путь в архиве")
            seen.add(normalized)
        for member in members:
            source.extract(member, destination)


def install(manifest, root, include_client, progress=print):
    validate_manifest(manifest)
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if include_client and not any(p["kind"] == "client" for p in manifest["packages"]):
        raise ValueError("В нашем релизе нет архива клиента Forever. Укажите установленный клиент.")
    version_dir = root / "packages" / manifest["version"]
    receipt = version_dir / "installed.json"
    if receipt.is_file():
        if json.loads(receipt.read_text(encoding="utf-8")) == manifest:
            if include_client and any(p["kind"] == "client" and not (version_dir / p["id"]).is_dir()
                                      for p in manifest["packages"]):
                raise ValueError("Эта версия установлена без архива клиента; используйте официальный лаунчер "
                                 "или отдельную папку установки. Существующие файлы не перезаписаны.")
            progress("Эта версия уже установлена; файлы клиента и сохранения не изменены")
            return version_dir
        raise ValueError("Версия уже существует с другим содержимым; требуется новый номер релиза")
    version_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=version_dir.parent) as temporary:
        stage = Path(temporary)
        for package in manifest["packages"]:
            if package["kind"] == "client" and not include_client:
                continue
            progress(f"Установка: {package['id']}")
            files = [download(part, root / "cache", progress) for part in package["parts"]]
            archive = stage / "package.download"
            with archive.open("wb") as output:
                for file in files:
                    with file.open("rb") as source:
                        shutil.copyfileobj(source, output, 1024 * 1024)
            if package["kind"] == "docker-image":
                subprocess.run(["docker", "load", "--input", str(archive)], check=True)
                subprocess.run(["docker", "image", "inspect", package["image"]],
                               check=True, stdout=subprocess.DEVNULL)
            else:
                destination = stage / package["id"]
                destination.mkdir()
                unpack_zip(archive, destination)
                if package["kind"] == "native-launcher" and not (destination / "forever-launcher.exe").is_file():
                    raise ValueError("В пакете отсутствует forever-launcher.exe")
                if package["kind"] == "client":
                    validate_client(destination, manifest["clientBuild"])
            archive.unlink()
        (stage / "installed.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        # Never overlay an existing installation, client, configuration or saves.
        if version_dir.exists():
            raise ValueError("Папка версии уже существует; автоматическая перезапись отключена")
        os.rename(stage, version_dir)
        # TemporaryDirectory cleanup tolerates a directory moved to its final name.
    return version_dir


def validate_client(root, expected_build):
    if not (root / "Data").is_dir() or not (root / "_classic_beta_/WowB.exe").is_file():
        raise ValueError("Нужна корневая папка Forever с Data, .build.info и _classic_beta_/WowB.exe")
    text = (root / ".build.info").read_text(encoding="utf-8-sig")
    rows = text.splitlines()
    if len(rows) < 2:
        raise ValueError("Не удалось прочитать .build.info")
    columns = [v.split("!", 1)[0] for v in rows[0].split("|")]
    for row in rows[1:]:
        values = dict(zip(columns, row.split("|")))
        if values.get("Product") == "wow_classic_beta" and values.get("Active") == "1":
            if values.get("Version") != f"1.60.1.{expected_build}":
                raise ValueError("Версия клиента не соответствует выбранному пакету")
            return
    raise ValueError("В .build.info нет активного клиента wow_classic_beta")
