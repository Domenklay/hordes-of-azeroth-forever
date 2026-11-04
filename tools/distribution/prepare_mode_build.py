#!/usr/bin/env python3
"""Prepare a disposable WoWForEver build context without editing reference repos."""
import argparse
from pathlib import Path
import shutil


def prepare(donor, repo):
    dockerfile = donor / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    anchor = "RUN python3 /tmp/inject-subclass.py /src/TrinityCore --module /subclass-module\n"
    if text.count(anchor) != 1:
        raise ValueError("Pinned Dockerfile injection anchor changed")
    text = text.replace(anchor, anchor + "\nCOPY hoa-mode /hoa-mode\n"
                        "COPY inject_hordes.py check_mode_compile.py /tmp/\n"
                        "RUN python3 /tmp/inject_hordes.py /src/TrinityCore --module /hoa-mode\n")
    anchor = "        -DCMAKE_BUILD_TYPE=Release \\\n"
    if text.count(anchor) != 1:
        raise ValueError("CMake build type anchor changed")
    text = text.replace(anchor, anchor + "        -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \\\n")
    anchor = '    && make -j"${BUILD_JOBS}" \\\n    && make install\n'
    if anchor not in text:
        raise ValueError("Pinned CMake build anchor changed")
    text = text.replace(anchor, '    && python3 /tmp/check_mode_compile.py /src/TrinityCore/build \\\n' + anchor, 1)
    # The injected syntax check is a separate shell command in the same RUN;
    # keep the original make/install chain intact.
    dockerfile.write_text(text, encoding="utf-8")
    shutil.copytree(repo / "mods/hordes-forever", donor / "hoa-mode", dirs_exist_ok=True)
    for name in ("inject_hordes.py", "check_mode_compile.py"):
        shutil.copyfile(repo / "tools/distribution" / name, donor / name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("donor", type=Path)
    args = parser.parse_args()
    prepare(args.donor.resolve(), Path(__file__).resolve().parents[2])
