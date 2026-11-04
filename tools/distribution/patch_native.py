#!/usr/bin/env python3
"""Adapt the pinned WoWForEver launcher for a per-installation local CA.

The original launcher, login and process-memory adapter remain upstream code.
This script is shipped with corresponding source in every distribution release.
"""
import json
from pathlib import Path
import sys


def replace(path, old, new, count=1):
    text = path.read_text(encoding="utf-8")
    if text.count(old) != count:
        raise ValueError(f"Pinned source changed: {path.name}: expected {count} matching fragments")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch(root):
    source = root / "app/src-tauri/src"
    config = source / "config.rs"
    text = config.read_text(encoding="utf-8")
    text += '''
// Hordes Offline: each installation generates its own CA; keys never ship.
pub fn ca_pem() -> &'static str {
    static LOCAL: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    LOCAL.get_or_init(|| match std::env::var_os("HOA_LOCAL_CA") {
        Some(path) => std::fs::read_to_string(path).expect("Cannot read local CA certificate"),
        None => CA_PEM.to_string(),
    }).as_str()
}
'''
    config.write_text(text, encoding="utf-8")
    replace(config, 'pub fn config_dir() -> PathBuf {', '''pub fn config_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("HOA_NATIVE_CONFIG") { return PathBuf::from(path); }''')
    replace(source / "api.rs", 'use crate::config::{Server, CA_PEM};', 'use crate::config::{Server, ca_pem};')
    replace(source / "api.rs", 'CA_PEM.as_bytes()', 'ca_pem().as_bytes()')
    replace(source / "system.rs", 'use crate::config::{Server, Settings, CA_PEM};', 'use crate::config::{Server, Settings, ca_pem};')
    replace(source / "system.rs", 'CA_PEM.lines()', 'ca_pem().lines()')
    replace(source / "system.rs", 'write_file("forever_root_ca.crt", CA_PEM)', 'write_file("forever_root_ca.crt", ca_pem())', 2)
    # UAC may not preserve custom environment variables: pass only the PUBLIC
    # certificate path explicitly to the elevated setup helper.
    replace(source / "system.rs", 'format!("--admin-setup {ip} {}", settings.server.portal)',
            'format!("--admin-setup {ip} {} \\\"{}\\\"", settings.server.portal, std::env::var("HOA_LOCAL_CA").unwrap_or_default())')
    replace(source / "main.rs", 'if args.len() == 4 && args[1] == "--admin-setup" {',
            '''if (args.len() == 4 || args.len() == 5) && args[1] == "--admin-setup" {
            if args.len() == 5 && !args[4].is_empty() { std::env::set_var("HOA_LOCAL_CA", &args[4]); }''')
    ui = root / "app/ui/i18n.js"
    translations = json.loads(Path(__file__).with_name("native-ru.json").read_text(encoding="utf-8"))
    replace(ui, "let lang = 'en';", "LANGUAGES.ru = 'Русский';\nTEXTS.ru = " + json.dumps(translations, ensure_ascii=False) + ";\nlet lang = 'ru';")
    # Keep original FR/EN/ES/PT. Russian falls back to English for diagnostic
    # strings that are not yet translated, rather than displaying empty text.
    rust = source / "i18n.rs"
    replace(rust, 'LANG.store(i, Ordering::Relaxed);',
            'RUSSIAN.store(lang.starts_with("ru"), Ordering::Relaxed);\n    LANG.store(i, Ordering::Relaxed);')
    replace(rust, 'static LANG: AtomicUsize = AtomicUsize::new(1);',
            'static LANG: AtomicUsize = AtomicUsize::new(1);\nstatic RUSSIAN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);')
    messages = {"step_connecting": "Подключение к локальному движку…", "step_preparing": "Подготовка игры…",
                "step_launching": "Запуск World of Warcraft…", "running": "Игра запущена: войдите в мир",
                "ready": "Ключ локального движка установлен: можно входить в мир", "closed": "Игра закрыта",
                "login_first": "Сначала войдите в локальную учётную запись", "bad_login": "Неверная почта или пароль",
                "already_running": "Игра уже запущена", "game_not_found": "Выберите папку клиента в настройках",
                "session_expired": "Войдите снова: срок сеанса истёк"}
    arms = "\n".join("            " + json.dumps(k) + " => return " + json.dumps(v, ensure_ascii=False) + "," for k, v in messages.items())
    replace(rust, "pub fn t(key: &str) -> &'static str {", "pub fn t(key: &str) -> &'static str {\n    if RUSSIAN.load(Ordering::Relaxed) {\n        match key {\n" + arms + "\n            _ => {}\n        }\n    }")
    conf = root / "app/src-tauri/tauri.conf.json"
    settings = json.loads(conf.read_text(encoding="utf-8"))
    settings["productName"] = "Hordes Forever Login"
    settings["identifier"] = "dev.hordes.forever.login"
    settings["app"]["windows"][0]["title"] = "Hordes Forever — вход в локальную игру"
    conf.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
