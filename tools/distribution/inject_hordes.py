#!/usr/bin/env python3
"""Install the maintained Hordes module into a pinned TrinityCore checkout."""
import argparse
from pathlib import Path
import re
import shutil


def inject(core, module):
    # The pinned donor already routes its own FSC1 requests to the session's
    # player before whisper target resolution. Extend that explicit endpoint
    # to our independent prefix: Forever can suppress self-addressed whispers.
    chat = core / "src/server/game/Handlers/ChatHandler.cpp"
    if chat.exists():
        handler = chat.read_text(encoding="utf-8")
        old = 'if (type == CHAT_MSG_WHISPER && prefix == "FSC1")'
        new = 'if (type == CHAT_MSG_WHISPER && (prefix == "FSC1" || prefix == "HOAQ1"))'
        if handler.count(old) == 1 and new not in handler:
            handler = handler.replace(old, new, 1)
        elif handler.count(new) != 1:
            raise ValueError("Pinned server-bound addon endpoint changed")
        chat.write_text(handler, encoding="utf-8")
    loader = core / "src/server/scripts/Custom/custom_script_loader.cpp"
    text = loader.read_text(encoding="utf-8")
    declaration = "void AddSC_hordes_forever();"
    if declaration not in text:
        anchor = r"(^void AddCustomScripts\(\)\s*\{)"
        if len(re.findall(anchor, text, re.M)) != 1:
            raise ValueError("Custom script loader layout changed")
        text = re.sub(anchor, declaration + "\n\n" + r"\1" + "\n    AddSC_hordes_forever();",
                      text, count=1, flags=re.M)
    if text.count("    AddSC_hordes_forever();") != 1:
        raise ValueError("Hordes registration must appear exactly once")
    loader.write_text(text, encoding="utf-8")
    target = loader.parent / "HordesForever"
    target.mkdir(exist_ok=True)
    for name in ("HordesForever.cpp", "HordesRules.h"):
        shutil.copyfile(module / "server" / name, target / name)
    for suffix, database in (("characters", "characters"), ("world", "world")):
        filename = f"2026_11_04_00_hordes_{suffix}.sql"
        destination = core / "sql/custom" / database / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(module / "sql" / filename, destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("core", type=Path)
    parser.add_argument("--module", type=Path, required=True)
    args = parser.parse_args()
    inject(args.core.resolve(), args.module.resolve())
