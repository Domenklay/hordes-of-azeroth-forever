"""Local Docker TTY attachment over Unix socket / Windows named pipe.

The CLI requires a host terminal for TTY stdin. Use Docker's documented
HTTP upgrade instead, and detach with the daemon's configured key sequence.
No signals, remote hosts, credentials or container lifecycle operations.
"""
import os
import re
import socket
import time
from urllib.parse import unquote, urlencode, urlsplit


class LocalConnection:
    def __init__(self, endpoint):
        address = urlsplit(endpoint)
        self.socket = self.pipe = None
        if address.query or address.fragment or address.netloc:
            raise ValueError("Диагностика поддерживает только локальный Docker Desktop")
        if address.scheme == "unix" and os.name != "nt":
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.settimeout(0.2)
            self.socket.connect(unquote(address.path))
        elif address.scheme == "npipe" and os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            path = unquote(address.path).replace("/", "\\")
            if not path.lower().startswith("\\\\.\\pipe\\"):
                raise ValueError("Ожидался локальный канал Docker Desktop")
            self.pipe = open(path, "r+b", buffering=0)
            self.handle = wintypes.HANDLE(msvcrt.get_osfhandle(self.pipe.fileno()))
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            self.kernel.PeekNamedPipe.restype = wintypes.BOOL
        else:
            raise ValueError("Для диагностики нужен локальный Docker Desktop (Unix socket или named pipe)")

    def send(self, data):
        if self.socket:
            self.socket.sendall(data)
        else:
            while data:
                written = self.pipe.write(data)
                if not written:
                    raise ConnectionError("Docker закрыл канал консоли")
                data = data[written:]

    def recv(self, maximum=65536):
        if self.socket:
            return self.socket.recv(maximum)
        import ctypes
        from ctypes import wintypes
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            available = wintypes.DWORD()
            if not self.kernel.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
                error = ctypes.get_last_error()
                if error in (109, 232, 233):
                    return b""
                raise ctypes.WinError(error)
            if available.value:
                return self.pipe.read(min(maximum, available.value))
            time.sleep(0.01)
        raise TimeoutError("Ожидание данных Docker")

    def close(self):
        if self.socket:
            self.socket.close()
        if self.pipe:
            self.pipe.close()


class TtyAttachment:
    def __init__(self, endpoint, identity):
        if not re.fullmatch(r"[0-9a-f]{12,64}", identity):
            raise ValueError("Некорректный идентификатор контейнера")
        self.connection = LocalConnection(endpoint)
        self.pending = b""
        try:
            parameters = urlencode(dict(stream=1, stdin=1, stdout=1, stderr=1, logs=0,
                                        detachKeys="ctrl-p,ctrl-q"))
            request = (f"POST /containers/{identity}/attach?{parameters} HTTP/1.1\r\n"
                       "Host: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: 0\r\n\r\n")
            self.connection.send(request.encode("ascii"))
            data, deadline = b"", time.monotonic() + 5
            while b"\r\n\r\n" not in data:
                if time.monotonic() >= deadline or len(data) > 16384:
                    raise TimeoutError("Docker не открыл канал консоли")
                try:
                    block = self.connection.recv()
                except TimeoutError:
                    continue
                if not block:
                    raise ConnectionError("Docker закрыл канал консоли")
                data += block
            header, self.pending = data.split(b"\r\n\r\n", 1)
            if not header.split(b"\r\n", 1)[0].startswith(b"HTTP/1.1 101 "):
                raise ConnectionError("Docker не подтвердил подключение к консоли")
        except Exception:
            self.connection.close()
            raise

    def send_line(self, command):
        self.connection.send((command + "\n").encode("utf-8"))

    def recv(self):
        if self.pending:
            result, self.pending = self.pending, b""
            return result
        return self.connection.recv()

    def detach(self):
        self.connection.send(b"\x10\x11")

    def close(self):
        self.connection.close()
