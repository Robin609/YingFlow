"""Verify the extracted release EXE with only native Windows tools on PATH."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("archive", type=Path)
args = parser.parse_args()
out = ROOT/"evidence"/("windows-package-"+uuid4().hex[:10])
unpacked = out/"unpacked"
unpacked.mkdir(parents=True)
archive_path = args.archive.resolve()
report = {"archive": str(archive_path), "evidence": str(out), "checks": []}
try:
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            if not (unpacked/entry.filename).resolve().is_relative_to(unpacked.resolve()):
                raise ValueError("Archive contains a path outside its folder")
            if Path(entry.filename).name.casefold() in {"local.settings.json", "app.db", "auth.json", "cookies", "login data"}:
                raise ValueError("Private local state unexpectedly included in archive")
        archive.extractall(unpacked)
    bundle = unpacked/"VideoWorkbench"
    for name in ("VideoWorkbench.exe", "VideoWorkbenchWorker.exe", "_internal/video_app/render/speech.ps1", "_internal/video_app/assets/speech/google-wavenet-b.mp3", "_internal/playwright/driver/node.exe"):
        if not (bundle/name).is_file():
            raise FileNotFoundError(name)
    if not list((bundle/"_internal/imageio_ffmpeg/binaries").glob("ffmpeg*.exe")):
        raise FileNotFoundError("Bundled FFmpeg")
    report["checks"].append("Extracted ZIP contains desktop/worker, speech samples/script, FFmpeg and Playwright; no private project/login files")
    system = Path(os.environ.get("SystemRoot", "C:/Windows"))
    env = {**os.environ, "PATH": os.pathsep.join(str(p) for p in (system/"System32", system, system/"System32/WindowsPowerShell/v1.0")),
           "PYTHONPATH": "", "PYTHONHOME": "", "PYTHONIOENCODING": "utf-8"}
    env.pop("QT_QPA_PLATFORM", None)
    with (out/"desktop.stdout.txt").open("wb") as stdout, (out/"desktop.stderr.txt").open("wb") as stderr:
        process = subprocess.Popen([str(bundle/"VideoWorkbench.exe"), "--smoke-check", str(out/"runtime")], cwd=bundle,
                                   env=env, stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
        report["pid"] = process.pid
        try:
            import time
            deadline = time.monotonic()+360
            while process.poll() is None:
                time.sleep(.2)
                error_text = (out/"desktop.stderr.txt").read_text(encoding="utf-8", errors="replace")
                if "Traceback (most recent call last)" in error_text:
                    subprocess.run([str(system/"System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    process.wait(timeout=15)
                    raise RuntimeError(error_text[-1800:])
                if time.monotonic()>deadline:
                    raise subprocess.TimeoutExpired(process.args, 360)
            code = process.returncode
        except subprocess.TimeoutExpired:
            subprocess.run([str(system/"System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            process.wait(timeout=15)
            raise TimeoutError("Owned package diagnostic exceeded its time budget and was stopped")
    report["exit_code"] = code
    diagnostic = json.loads((out/"runtime/report.json").read_text(encoding="utf-8"))
    report["runtime"] = diagnostic
    if code or diagnostic["status"] != "passed" or not diagnostic["frozen"] or diagnostic["qt_platform"] != "windows":
        raise RuntimeError(diagnostic.get("error") or f"Packaged runtime exit {code}")
    report["checks"].append("Actual extracted windowed EXE passes with Python, Node and project tools removed from PATH")
    report["status"] = "passed"
except Exception as error:
    report.update(status="failed", error=repr(error))
finally:
    (out/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT/"evidence/windows-package-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(out/"report.json"), "error": report.get("error")}, ensure_ascii=False))
raise SystemExit(0 if report["status"] == "passed" else 1)
