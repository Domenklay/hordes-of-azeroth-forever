"""Real Tk geometry/clipboard regression, run on the Windows build runner."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(sys.platform == "win32" or os.environ.get("DISPLAY"), "Tk display unavailable")
class LauncherGuiTests(unittest.TestCase):
    def test_large_engine_log_keeps_copy_visible_and_full_clipboard_at_small_window(self):
        import tkinter as tk
        from forever_setup import Launcher
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
            window = tk.Tk()
            try:
                window.tk.call("tk", "scaling", 1.5)
                launcher = Launcher(window)
                window.geometry("700x620")
                output = "\n".join(f"worldserver | diagnostic line {i}" for i in range(3000))
                launcher.events.put(("engine_log", output))
                launcher.poll()
                window.update()
                button = launcher.copy_button
                self.assertTrue(button.winfo_viewable())
                self.assertGreaterEqual(button.winfo_rooty(), window.winfo_rooty())
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), window.winfo_rooty() + window.winfo_height())
                self.assertEqual(launcher.notebook.select(), str(launcher.logs_page))
                self.assertNotIn("\n", launcher.status.get())
                button.invoke()
                self.assertEqual(window.clipboard_get(), output)
                self.assertEqual(launcher.log.get("1.0", "end-1c").rstrip("\n"), output)
            finally:
                window.destroy()
