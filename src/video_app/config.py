"""Resolve executable paths before changing any child-process environment."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys


LOCAL_SETTINGS = Path(__file__).resolve().parents[2] / "local.settings.json"


def load_settings(path: Path | None = None, overrides: dict | None = None) -> dict:
    # Installation-local defaults are shared by double-click and CLI launches;
    # otherwise Windows app-data redirection can create a second empty profile.
    settings = json.loads(LOCAL_SETTINGS.read_text(encoding="utf-8-sig")) if not getattr(sys, "frozen", False) and LOCAL_SETTINGS.is_file() else {}
    if path:
        settings.update(json.loads(path.read_text(encoding="utf-8-sig")))
    overrides = dict(overrides or {})
    if not overrides.get("gemini_profile_dir") and settings.get("gemini_profile_dir"):
        overrides.pop("gemini_profile_dir", None)
    settings.update(overrides)
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    settings["data_root"] = str(Path(settings.get("data_root") or local / "VideoMaker").resolve())
    settings.setdefault("browser_port", 9222)
    settings.setdefault("browser_auto_port", True)
    settings["agy_model"] = settings.get("agy_model") or "gemini-3.8-flash-medium"
    settings["codex_model"] = settings.get("codex_model") or "gpt-6.1-sol"
    settings["gemini_mode"] = (settings.get("gemini_mode") or "4 Argon").strip() or "4 Argon"
    candidates = {
        "agy_executable": [shutil.which("agy"), home / ".gemini/bin/agy.exe"],
        "codex_executable": [shutil.which("codex")],
        "chrome_executable": [
            shutil.which("chrome"),
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            local / "Google/Chrome/Application/chrome.exe",
        ],
    }
    for key, paths in candidates.items():
        # An explicitly configured missing path is an error, not a silent fallback.
        paths = [settings[key]] if settings.get(key) else paths
        settings[key] = next((str(Path(p).resolve()) for p in paths if p and Path(p).is_file()), None)
    return settings


def browser_setup_defaults(root: Path, overrides: dict | None = None) -> dict:
    """Find this computer's installation and keep only a usable local profile."""
    settings = load_settings(overrides=overrides)
    if not settings.get("chrome_executable"):
        settings["chrome_executable"] = load_settings(overrides={"chrome_executable": None})["chrome_executable"]
    profile = settings.get("gemini_profile_dir")
    settings["gemini_profile_dir"] = str(Path(profile).resolve() if profile and Path(profile).is_dir()
                                        else (Path(root) / "browser/gemini").resolve())
    return {key: settings[key] for key in ("chrome_executable", "gemini_profile_dir", "browser_port", "browser_auto_port", "gemini_mode")}
