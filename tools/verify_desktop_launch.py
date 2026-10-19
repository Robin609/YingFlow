"""Launch the real Windows surface, capture it once, then leave it usable."""
import json
import os
import argparse
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from video_app.config import load_settings
from video_app.ui.window import Window, run_app


ROOT = Path(__file__).resolve().parents[1]
settings = load_settings()
parser = argparse.ArgumentParser()
parser.add_argument("--data-root", type=Path)
parser.add_argument("--close-after-capture", action="store_true")
parser.add_argument("--page", type=int, default=0)
args = parser.parse_args()
app = QApplication([])


def capture():
    window = next((w for w in app.topLevelWidgets() if isinstance(w, Window)), None)
    if window is None:
        return
    window.navigate(args.page)
    app.processEvents()
    screenshot = ROOT / "evidence/desktop-window.png"
    saved = window.grab().save(str(screenshot))
    record = {"scope": "native_windows_desktop_startup", "pid": os.getpid(),
              "visible": window.isVisible(), "title": window.windowTitle(),
              "screenshot_saved": saved, "screenshot": str(screenshot),
              "data_root": str(window.engine.root), "platform": app.platformName()}
    record["generated_at"] = datetime.now(timezone.utc).isoformat()
    record["background_thread_running"] = bool(window.tasks and window.tasks.isRunning())
    record["automatic_recommendations"] = False
    (ROOT / "evidence/desktop-launch.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.close_after_capture:
        window.close()


QTimer.singleShot(1200, capture)
raise SystemExit(run_app(args.data_root or Path(settings["data_root"]), auto_recommendations=False))
