#!/usr/bin/env python3
"""Split local image archives into GitHub assets and generate checked metadata."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from installer import REPOSITORY, validate_manifest

PART_SIZE = 1024 * 1024 * 1024  # GitHub allows 2 GiB per asset; stay below it.


def package(source, identity, kind, output, tag, image=None, part_size=PART_SIZE):
    parts = []
    with source.open("rb") as stream:
        index = 0
        while True:
            block = stream.read(min(1024 * 1024, part_size))
            if not block:
                break
            name = f"{identity}.{index:03d}.part"
            target = output / name
            size, digest = 0, hashlib.sha256()
            with target.open("wb") as file:
                while block:
                    file.write(block)
                    size += len(block)
                    digest.update(block)
                    if size == part_size:
                        break
                    block = stream.read(min(1024 * 1024, part_size - size))
            parts.append({"url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}",
                          "size": size, "sha256": digest.hexdigest()})
            index += 1
    result = {"id": identity, "kind": kind, "parts": parts}
    if image:
        result["image"] = image
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--build", type=int, choices=(70450, 70532), default=70532)
    parser.add_argument("--core-image", help="Reuse the exact image tag from an earlier, unchanged engine build")
    parser.add_argument("--survivor", action="store_true", help="Package the first actual survivor mode")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", args.tag):
        parser.error("Invalid release tag")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": 1, "version": args.version, "clientBuild": args.build,
                "status": "base-forever-preview-not-hordes-port", "packages": [
        package(args.input / "core.tar.gz", "core", "docker-image", args.output, args.tag,
                args.core_image or "hoa-forever-core:" + args.version),
        package(args.input / "mysql.tar.gz", "mysql", "docker-image", args.output, args.tag, "mysql:8.0"),
        package(args.input / "game-launcher.zip", "game-launcher", "native-launcher", args.output, args.tag),
    ]}
    if args.survivor:
        manifest.update(status="survivor-solo-prototype", modeProtocol=1)
        addon = args.input / "hordes-addon.zip"
        source = Path(__file__).resolve().parents[2] / "mods/hordes-forever/addon"
        with zipfile.ZipFile(addon, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, str(path.relative_to(source)))
        manifest["packages"].append(package(addon, "hordes-addon", "addon", args.output, args.tag))
    validate_manifest(manifest)
    (args.output / "offline-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
