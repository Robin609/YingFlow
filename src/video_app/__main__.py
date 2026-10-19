"""Desktop entry point plus an explicit, non-generating connection preflight."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from .config import load_settings


def preflight(settings: dict) -> dict:
    checks = {}
    for name in ("agy", "codex", "chrome"):
        path = settings.get(f"{name}_executable")
        row = {"path": path, "status": "DETECTED" if path else "CLI_NOT_FOUND"}
        if path and name != "chrome":
            try:
                result = subprocess.run([path, "--version"], capture_output=True,
                                        timeout=15, shell=False)
                row.update(exit_code=result.returncode,
                           version=result.stdout.decode("utf-8", errors="replace").strip(),
                           status="VERSION_OK" if result.returncode == 0 else "CLI_EXIT_ERROR")
            except subprocess.TimeoutExpired:
                row["status"] = "PROCESS_TIMEOUT"
            except OSError as error:
                row.update(status="PROCESS_START_FAILED", message=str(error))
        checks[name] = row
    return {"scope": "path_and_version_only", "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks, "generation_verified": False,
            "browser_send_read_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="视频制作工作台")
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--output", type=Path, help="将检测报告保存为 JSON")
    parser.add_argument("--data-root", type=Path, help="项目数据目录；默认使用本机应用数据目录")
    parser.add_argument("--render-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-check", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("command", nargs="?", default="gui", choices=["gui", "preflight"])
    args = parser.parse_args()
    if args.render_worker:
        from .render.worker import main as render_main
        return render_main(args.render_worker)
    if args.smoke_check:
        from .diagnostics import run_check
        return run_check(args.smoke_check)
    settings = load_settings(args.settings)
    if args.command == "gui":
        from .ui.window import run_app
        return run_app(args.data_root or Path(settings["data_root"]), settings if args.settings else None)
    report = preflight(settings)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if all(row["status"] in {"VERSION_OK", "DETECTED"}
                    for row in report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
