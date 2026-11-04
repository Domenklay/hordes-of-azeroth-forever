#!/usr/bin/env python3
"""Russian Windows bootstrapper; PyInstaller bundles Python and Tk."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from distribution import installer, runtime, diagnostics


class Launcher:
    def __init__(self, window):
        self.window = window
        window.title("Hordes Forever 0.2.0-preview.5 — локальная игра")
        window.geometry("850x720")
        window.minsize(700, 620)
        self.events = queue.Queue()
        self.busy = False
        self.native = None
        self.vendor = None
        self.root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HordesForever"
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.root / "launcher.json"
        self.settings = json.loads(self.settings_path.read_text(encoding="utf-8")) if self.settings_path.exists() else {}
        self.client = tk.StringVar(value=self.settings.get("client", ""))
        self.status = tk.StringVar(value="Первичная настройка требует интернета. Затем запускайте локально.")
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Hordes of Azeroth Forever", font=("Segoe UI", 20)).pack(anchor="w")
        ttk.Label(frame, text="Одиночный прототип • волны, автоатаки и выбор улучшений").pack(anchor="w", pady=(0, 12))
        # Allocate the footer before the expanding content. Long logs must
        # never push copy/help controls outside the visible client area.
        footer = ttk.Frame(frame)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        self.copy_button = ttk.Button(footer, text="Скопировать журнал", command=self.copy_log)
        self.copy_button.pack(side="right")
        ttk.Button(footer, text="Инструкция / Docker Desktop", command=lambda: webbrowser.open(
            "https://github.com/" + installer.REPOSITORY + "/blob/main/config/offline/LAUNCHER.ru.md")).pack(side="left")
        status_label = ttk.Label(frame, textvariable=self.status, wraplength=780)
        status_label.pack(side="bottom", fill="x", pady=(8, 0))
        frame.bind("<Configure>", lambda event: status_label.configure(wraplength=max(300, event.width)))
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill="both", expand=True)
        controls = ttk.Frame(self.notebook, padding=(0, 8))
        self.logs_page = ttk.Frame(self.notebook, padding=(0, 8))
        self.notebook.add(controls, text="Игра и установка")
        self.notebook.add(self.logs_page, text="Журнал")
        frame = controls
        ttk.Label(frame, text="Клиент Forever: корневая папка с Data и _classic_beta_").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=5)
        ttk.Entry(row, textvariable=self.client).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Выбрать…", command=self.choose_client).pack(side="left", padx=5)
        self.include_client = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Скачать клиент из нашего релиза, если архив опубликован",
                        variable=self.include_client).pack(anchor="w")
        self.buttons = []
        for label, callback in [
            ("Установить компоненты из нашего GitHub", self.install),
            ("Скачать клиент через ForeverBetaLauncher.exe…", self.official),
            ("Запустить локальный движок", self.start),
            ("Войти в игру", self.play),
            ("Создать аккаунт / открыть консоль", self.account),
            ("Остановить локальный движок", self.stop),
            ("Журнал движка", self.logs),
            ("Состояние движка", self.engine_status),
        ]:
            button = ttk.Button(frame, text=label, command=lambda cb=callback: self.work(cb))
            button.pack(fill="x", pady=3)
            self.buttons.append(button)
        self.logs_page.rowconfigure(1, weight=1)
        self.logs_page.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(self.logs_page)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        refresh = ttk.Button(toolbar, text="Обновить журнал движка", command=lambda: self.work(self.logs))
        refresh.pack(side="left")
        self.buttons.append(refresh)
        trace = ttk.Button(toolbar, text="Проверить обмен с игрой (20 сек)", command=lambda: self.work(self.trace))
        trace.pack(side="left", padx=8)
        self.buttons.append(trace)
        self.log = tk.Text(self.logs_page, height=8, wrap="word", state="disabled")
        self.log.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.logs_page, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)
        window.after(150, self.poll)
        window.protocol("WM_DELETE_WINDOW", self.close)

    def choose_client(self):
        folder = filedialog.askdirectory(title="Корень клиента Forever (Data и .build.info)")
        if folder:
            self.client.set(folder)

    def copy_log(self):
        text = self.log.get("1.0", "end-1c")
        if not text.strip():
            self.status.set("Журнал пока пуст.")
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self.window.update_idletasks()
        self.status.set("Журнал скопирован в буфер обмена.")

    def save_client(self, client):
        self.settings["client"] = str(client)
        self.settings_path.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def progress(self, text):
        self.events.put(("log", text))

    def work(self, callback):
        if self.busy:
            return
        # Tk variables must be read on the UI thread, before starting workers.
        self.client_path = Path(self.client.get()).resolve() if self.client.get().strip() else None
        self.download_client = self.include_client.get()
        self.busy = True
        for button in self.buttons:
            button.state(["disabled"])
        def worker():
            try:
                callback()
            except Exception as error:
                detail = str(error)
                if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                    detail += "\n" + error.stderr[-4000:]
                self.events.put(("error", detail))
            finally:
                self.events.put(("done", ""))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "done":
                    self.busy = False
                    for button in self.buttons:
                        button.state(["!disabled"])
                elif kind == "client":
                    self.client.set(text)
                elif kind == "official":
                    filename = filedialog.askopenfilename(title="Ваш официальный ForeverBetaLauncher.exe",
                                                         filetypes=[("Forever Beta Launcher", "*.exe")])
                    if filename:
                        self.vendor = subprocess.Popen([filename], cwd=Path(filename).parent)
                        self.progress("После загрузки клиента закройте официальный лаунчер и выберите папку клиента здесь.")
                else:
                    # Keep the full output in the text widget. A Label that
                    # contains hundreds of lines changes the whole layout.
                    first_line = next((line for line in text.splitlines() if line.strip()), "")
                    self.status.set("Журнал движка обновлён." if kind == "engine_log" else first_line[:180])
                    self.log.configure(state="normal")
                    self.log.insert("end", ("Ошибка: " if kind == "error" else "") + text + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                    if kind in ("engine_log", "error"):
                        self.notebook.select(self.logs_page)
        except queue.Empty:
            pass
        self.window.after(150, self.poll)

    def installed(self):
        current = self.root / "current.json"
        if not current.is_file():
            raise ValueError("Сначала установите компоненты из GitHub")
        manifest = installer.validate_manifest(json.loads(current.read_text(encoding="utf-8")))
        return self.root / "packages" / manifest["version"], manifest

    def install(self):
        if self.native is not None and self.native.poll() is None:
            raise ValueError("Сначала закройте игру и игровой лаунчер")
        subprocess.run(["docker", "info", "--format", "{{.OSType}}"], check=True, capture_output=True)
        self.progress("Чтение нашего релиза…")
        try:
            manifest = installer.fetch_manifest()
        except Exception as error:
            raise ValueError("Не удалось получить offline-manifest.json. Проверьте интернет; "
                             "возможно, сборка пакетов в GitHub Actions ещё не завершена.") from error
        installer.install(manifest, self.root, self.download_client, self.progress)
        if self.download_client:
            package = next(p for p in manifest["packages"] if p["kind"] == "client")
            client = self.root / "packages" / manifest["version"] / package["id"]
            self.save_client(client)
            self.events.put(("client", str(client)))
        # Activate only after complete installation. Keep the previous version.
        temporary = self.root / "current.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.root / "current.json")
        self.progress("Компоненты установлены. Выберите клиент и запустите локальный движок.")

    def official(self):
        # Choose the existing vendor launcher; never reinterpret its
        # signed release metadata or fetch unverified game chunks ourselves.
        self.events.put(("official", ""))

    def start(self):
        if self.vendor is not None and self.vendor.poll() is None:
            raise ValueError("Дождитесь окончания обновления и закройте официальный лаунчер")
        if self.client_path is None:
            raise ValueError("Выберите папку клиента или скачайте его официальным лаунчером")
        version, manifest = self.installed()
        runtime.prepare(self.root, version, manifest, self.client_path, self.progress)
        self.save_client(self.client_path)
        runtime.engine(self.root, "start", self.progress)
        self.engine_status()

    def engine_status(self):
        report = runtime.status(self.root)
        self.progress(report["message"] + "\n" + report["details"])
        if report["failed"]:
            self.events.put(("engine_log", runtime.engine(self.root, "logs")))

    def play(self):
        if self.client_path is None:
            raise ValueError("Выберите папку клиента")
        if self.native is not None and self.native.poll() is None:
            raise ValueError("Игровой лаунчер уже открыт")
        version, manifest = self.installed()
        self.native = runtime.launch(self.root, version, manifest, self.client_path, self.progress)
        self.progress("Открыт игровой лаунчер. При первом запуске нажмите «Подготовить ПК» и войдите "
                      "с локальным аккаунтом. Игровая проверка этой сборки ещё не выполнена.")
        if manifest.get("modeProtocol") == 1:
            self.progress("После входа персонажем откроется меню Hordes: нажмите «Начать забег». "
                          "Открыть меню повторно: /hordes. Управление WASD, атаки автоматические.")

    def account(self):
        runtime.console(self.root)
        self.progress("В консоли: bnetaccount create ваш@email ваш_пароль. "
                      "Не прерывайте первый импорт. Отсоединение без остановки: Ctrl+P, Ctrl+Q.")

    def stop(self):
        if self.native is not None and self.native.poll() is None:
            raise ValueError("Сначала выйдите из игры и закройте игровой лаунчер")
        runtime.engine(self.root, "stop")
        self.progress("Движок остановлен; база и персонажи сохранены.")

    def logs(self):
        self.events.put(("engine_log", runtime.engine(self.root, "logs")))

    def trace(self):
        self.events.put(("engine_log", diagnostics.run(self.root, self.progress)))

    def close(self):
        if self.busy:
            messagebox.showinfo("Выполняется операция", "Дождитесь завершения установки или подготовки данных.")
            return
        if self.native is not None and self.native.poll() is None:
            messagebox.showinfo("Игра открыта", "Сначала закройте игру и игровой лаунчер.")
            return
        self.window.destroy()


def main():
    if sys.platform != "win32":
        raise SystemExit("Этот установщик предназначен для Windows x64 с Docker Desktop (Linux containers).")
    window = tk.Tk()
    # Avoid two installers sharing a cache, profile or extraction directory.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\HordesForeverSetup")
    if not handle:
        raise OSError(ctypes.get_last_error(), "Не удалось создать блокировку установщика")
    if ctypes.get_last_error() == 183:
        messagebox.showinfo("Установщик уже открыт", "Используйте открытое окно Hordes Forever.")
        kernel.CloseHandle(handle)
        window.destroy()
        return
    try:
        Launcher(window)
        window.mainloop()
    finally:
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
