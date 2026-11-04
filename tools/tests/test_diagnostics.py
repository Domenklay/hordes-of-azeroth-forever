import os
from pathlib import Path
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from distribution import diagnostics
from distribution.docker_attach import LocalConnection, TtyAttachment


class DiagnosticsTests(unittest.TestCase):
    def test_report_contains_only_opcode_identifiers_not_chat_or_tokens(self):
        lines = [
            "private account password not for the report",
            "C->S: classic opcode 0x2D002B translated to [CMSG_CHAT_ADDON_MESSAGE_TARGETED 0x2C002B (123)]",
            "C->S: 172.18.0.1 [CMSG_CHAT_MESSAGE_SAY 0x2C0023]",
            "WorldSocket wrong opcode (opcode: 0x44, size: 0) data: private-token 0xabcdef",
            "WorldSession ByteBufferException (pos: 5 size: 5) (opcode: [CMSG_TEST 0x123]) from [Player: private-name]",
        ]
        text = "\n".join(diagnostics.clean_lines(lines))
        self.assertIn("CMSG_CHAT_ADDON_MESSAGE_TARGETED", text)
        self.assertIn("0x2D002B", text)
        self.assertIn("pos: 5 size: 5", text)
        self.assertNotIn("private", text)
        self.assertNotIn("172.18", text)
        self.assertNotIn("0xabcdef", text)

    def test_collection_restores_levels_and_detaches_when_ui_callback_fails(self):
        class Channel:
            instance = None
            def __init__(self, *_): self.commands=[]; self.detached=False; self.closed=False; Channel.instance=self
            def recv(self):
                import time
                time.sleep(.01)
                raise TimeoutError()
            def send_line(self, command): self.commands.append(command)
            def detach(self): self.detached=True
            def close(self): self.closed=True
        def fail(_): raise RuntimeError("UI closed")
        with patch.object(diagnostics, "public_configuration", return_value=({}, 5, 3)), \
                patch.object(diagnostics, "TtyAttachment", Channel):
            with self.assertRaisesRegex(RuntimeError, "UI closed"):
                diagnostics.collect("unix:///fixture", "a"*64, fail, seconds=.1)
        self.assertEqual(Channel.instance.commands[-2:], ["server set loglevel l network.opcode 5", "server set loglevel a Console 3"])
        self.assertTrue(Channel.instance.detached and Channel.instance.closed)

    def test_remote_docker_endpoints_are_not_used(self):
        for endpoint in ("tcp://example.test:2375", "ssh://example.test", "npipe://remote/pipe/docker"):
            with self.assertRaises(ValueError): LocalConnection(endpoint)

    @unittest.skipUnless(os.name == "nt", "Windows named pipe transport")
    def test_windows_named_pipe_http_upgrade_and_clean_detach(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,
                                          wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
        kernel.CreateNamedPipeW.restype = wintypes.HANDLE
        kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE,ctypes.c_void_p]
        kernel.ReadFile.argtypes = [wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
        kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
        kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        name = "hoa-diagnostic-test-" + uuid.uuid4().hex
        handle = kernel.CreateNamedPipeW("\\\\.\\pipe\\"+name,3,0,1,4096,4096,1000,None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        completed, received, failures = threading.Event(), [], []
        def server():
            try:
                if not kernel.ConnectNamedPipe(handle,None) and ctypes.get_last_error()!=535:
                    raise ctypes.WinError(ctypes.get_last_error())
                def read():
                    buffer=ctypes.create_string_buffer(4096); count=wintypes.DWORD()
                    if not kernel.ReadFile(handle,buffer,4096,ctypes.byref(count),None):
                        raise ctypes.WinError(ctypes.get_last_error())
                    return buffer.raw[:count.value]
                data=b""
                while b"\r\n\r\n" not in data: data+=read()
                self.assertIn(b"detachKeys=ctrl-p%2Cctrl-q",data)
                payload=b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\nC->S: PIPE_TEST\n"
                count=wintypes.DWORD()
                if not kernel.WriteFile(handle,payload,len(payload),ctypes.byref(count),None):
                    raise ctypes.WinError(ctypes.get_last_error())
                data=b""
                while b"\x10\x11" not in data: data+=read()
                received.append(data)
            except Exception as error: failures.append(error)
            finally:
                kernel.DisconnectNamedPipe(handle); kernel.CloseHandle(handle); completed.set()
        worker=threading.Thread(target=server,daemon=True);worker.start()
        connection=TtyAttachment("npipe:////./pipe/"+name,"a"*64)
        try:
            connection.send_line("probe")
            import time
            content, deadline = b"", time.monotonic()+3
            while b"PIPE_TEST" not in content and time.monotonic()<deadline:
                try: content+=connection.recv()
                except TimeoutError: pass
            self.assertIn(b"PIPE_TEST",content)
            connection.detach()
            self.assertTrue(completed.wait(3))
        finally: connection.close()
        if failures: raise failures[0]
        self.assertIn(b"probe\n",received[0])
        self.assertTrue(received[0].endswith(b"\x10\x11"))
