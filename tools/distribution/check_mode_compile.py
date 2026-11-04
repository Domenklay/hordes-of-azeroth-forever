#!/usr/bin/env python3
"""Check the real adapter with CMake's own flags before a costly full build."""
import json
import re
from pathlib import Path
import shlex
import subprocess
import sys

root = Path(sys.argv[1])
entries = json.loads((root / "compile_commands.json").read_text())
found = [entry for entry in entries if entry["file"].endswith("HordesForever.cpp")]
if len(found) != 1:
    raise SystemExit("Hordes adapter was not collected exactly once by CMake")
entry = found[0]
arguments = entry.get("arguments") or shlex.split(entry["command"])
command, index = [], 0
while index < len(arguments):
    value = arguments[index]
    if value == "-o":
        index += 2
        continue
    if value == "-c":
        command.append("-fsyntax-only")
    else:
        command.append(value)
    index += 1
result = subprocess.run(command, cwd=entry["directory"], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
print(result.stdout)
if result.returncode:
    errors = "\n".join(line for line in result.stdout.splitlines() if re.search(r"(?:fatal )?error:", line))
    diagnostic = (errors or result.stdout[-3000:])[:3500].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print("::error title=Hordes TrinityCore API check::" + diagnostic)
sys.exit(result.returncode)
