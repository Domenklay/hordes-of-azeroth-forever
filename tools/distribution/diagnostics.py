"""Short, reversible opcode-name trace for a single local world process."""
import codecs
from collections import deque
import re
import threading
import time

import offline
from .docker_attach import TtyAttachment

PUBLIC_KEYS = ("Logger.network.opcode", "Appender.Console", "AddonChannel", "Hordes.Enable")


def public_configuration(identity):
    # Never return the complete server config: it contains database passwords.
    script = r"awk -F= '/^[[:space:]]*(Logger\.network\.opcode|Appender\.Console|AddonChannel|Hordes\.Enable)[[:space:]]*=/ {print $0}' /opt/trinitycore/etc/worldserver.conf"
    text = offline.capture_command(["docker", "exec", identity, "sh", "-c", script]).stdout
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in PUBLIC_KEYS:
            values[key.strip()] = value.strip()
    try:
        logger = int(values["Logger.network.opcode"].split(",")[0])
        console = int(values["Appender.Console"].split(",")[1])
    except (KeyError, IndexError, ValueError) as error:
        raise ValueError("Не удалось прочитать настройки журнала для последующего восстановления") from error
    if logger not in range(7) or console not in range(7):
        raise ValueError("Неизвестные уровни журнала; диагностика не включалась")
    public = {"Logger.network.opcode": str(logger), "Appender.Console": str(console)}
    for key in ("AddonChannel", "Hordes.Enable"):
        public[key] = values.get(key) if values.get(key) in ("0", "1", "true", "false") else "?"
    return public, logger, console


def clean_lines(lines):
    output = []
    for line in lines:
        line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
        name = re.search(r"\bCMSG_[A-Z0-9_]+\b", line)
        codes = re.findall(r"\b0x[0-9a-fA-F]{1,8}\b", line)
        if "C->S:" in line and (name or codes):
            output.append("C->S: " + (name.group() if name else "UNKNOWN") + " | " + " → ".join(codes[:2]))
        elif any(word in line for word in ("ByteBufferException", "wrong opcode", "No defined handler")):
            position = re.search(r"pos:\s*\d+\s+size:\s*\d+", line)
            output.append("Ошибка чтения: " + (name.group() if name else "UNKNOWN") + " | " +
                          " ".join(codes[:1]) + (" | " + position.group() if position else ""))
    return output


def collect(endpoint, identity, progress=lambda _: None, seconds=20):
    if not 0 < seconds <= 30:
        raise ValueError("Диагностическая запись ограничена 30 секундами")
    values, previous_logger, previous_console = public_configuration(identity)
    attachment = TtyAttachment(endpoint, identity)
    stopped = threading.Event()
    lock = threading.Lock()
    captured = deque(maxlen=6000)
    read_errors = []
    def read():
        decoder, pending = codecs.getincrementaldecoder("utf-8")("replace"), ""
        try:
            while not stopped.is_set():
                try:
                    block = attachment.recv()
                except TimeoutError:
                    continue
                if not block:
                    break
                pending += decoder.decode(block)
                lines = pending.split("\n")
                pending = lines.pop()
                with lock:
                    captured.extend(line.rstrip("\r") for line in lines)
        except OSError:
            read_errors.append("Канал консоли был закрыт во время проверки")
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    restored = False
    try:
        attachment.send_line("server set loglevel l network.opcode 1")
        attachment.send_line("server set loglevel a Console 1")
        progress("Запись 20 секунд: в игре нажмите «Проверить связь», затем введите .hoa status в чат.")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    finally:
        try:
            attachment.send_line(f"server set loglevel l network.opcode {previous_logger}")
            attachment.send_line(f"server set loglevel a Console {previous_console}")
            time.sleep(0.4)  # allow the CLI to consume restoration before detaching
            restored = True
            attachment.detach()
        finally:
            stopped.set()
            reader.join(timeout=1)
            attachment.close()
    with lock:
        result = clean_lines(list(captured))
    if not restored:
        raise ValueError("Не подтверждено восстановление журнала. Перезапустите локальный движок.")
    header = ["=== HOA: проверка обмена с игрой ===",
              "Конфигурация: " + "; ".join(f"{key}={values.get(key, '?')}" for key in PUBLIC_KEYS),
              f"Строк входящего обмена: {len(result)}",
              "Отправлены команды восстановления журнала. В отчёте только названия и номера пакетов, без их содержимого."]
    if not result:
        header.append("Входящих пакетов за время проверки не обнаружено. Проверьте, что персонаж вошёл в мир.")
    return "\n".join(header + read_errors + result + ["=== Конец проверки ==="])


def run(root, progress=lambda _: None):
    _, env, command = offline.configuration(root / "profile")
    project = command[command.index("--project-name") + 1]
    identity = offline.capture_command(["docker", "ps", "--quiet", "--filter",
        "label=com.docker.compose.project=" + project, "--filter", "label=com.docker.compose.service=worldserver"], env=env).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{12,64}", identity):
        raise ValueError("Не найден единственный запущенный мир этого локального профиля")
    flags = offline.capture_command(["docker", "inspect", "--format",
        "{{.Config.Tty}} {{.Config.OpenStdin}} {{.Config.StdinOnce}}", identity]).stdout.strip()
    if flags != "true true false":
        raise ValueError("Настройки консоли не подтверждают безопасное диагностическое подключение; проверка не включалась")
    probe = offline.capture_command(["docker", "exec", identity, "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/8085"], check=False)
    if probe.returncode:
        raise ValueError("Игровой мир ещё не открыл внутренний порт. Дождитесь готовности и повторите проверку.")
    before = offline.capture_command(["docker", "inspect", "--format", "{{.State.StartedAt}} {{.Config.Image}}", identity]).stdout.strip()
    endpoint = env.get("DOCKER_HOST") if not env.get("DOCKER_CONTEXT") else None
    if not endpoint:
        endpoint = offline.capture_command(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], env=env).stdout.strip()
    report = collect(endpoint, identity, progress)
    after = offline.capture_command(["docker", "inspect", "--format", "{{.State.StartedAt}} {{.Config.Image}}", identity]).stdout.strip()
    if after != before:
        report += "\nЗа время проверки процесс мира изменился; повторите проверку после его готовности."
    return "Мир: " + before + "\n" + report
