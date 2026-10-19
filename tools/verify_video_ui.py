"""Focused local-video acceptance; real MP4/voice, no model or paid calls."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
from threading import Event
import time
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from video_app.core.engine import Engine
from video_app.core.production import VideoProduction
from video_app.core.workflow import WorkflowError
from video_app.ui.tasks import TaskService
from video_app.ui.theme import load_system_fonts
from video_app.ui.window import Window

parser = argparse.ArgumentParser()
parser.add_argument("--source-export", type=Path, required=True)
parser.add_argument("--quality", choices=["720", "1080"], default="1080")
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence" / ("video-ui-" + uuid4().hex[:10])
OUT.mkdir(parents=True)
source = json.loads((args.source_export / "project.json").read_text(encoding="utf-8"))
engine = Engine(OUT / "data")
p = engine.create_project(source["project"]["name"], source["project"]["settings"], "full_video")
for stage in ("topic", "draft", "review", "final", "storyboard"):
    published_id = next(s["output_artifact_id"] for s in source["steps"] if s["stage"] == stage)
    payload = next(v["payload"] for v in source["versions"][stage] if v["id"] == published_id)
    engine.save_stage(p, stage, payload, confirm=True)
upstream = {s:engine.step(p,s)["output_artifact_id"] for s in ("topic","draft","review","final","storyboard")}
model_attempts_before = engine._one("SELECT COUNT(*) AS n FROM attempts WHERE provider!='local'")["n"]
engine.save_settings({"video_options": {"quality": args.quality, "rate": 1, "voice": ""}})
checks = []
report = {"source_export": str(args.source_export.resolve()), "scope": "real_local_video_from_confirmed_export", "model_requests": 0, "checks": checks}
app = QApplication([])
app.setStyle("Fusion")
load_system_fonts()
tasks = TaskService(engine.root)
window = Window(engine, tasks, auto_recommendations=False)


def check(value, message):
    if not value:
        raise AssertionError(message)
    checks.append(message)


def wait(predicate, seconds=900):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        latest = VideoProduction(engine).latest(p)
        if latest and latest["state"] == "FAILED":
            raise RuntimeError(latest["error"])
        time.sleep(.04)
    raise TimeoutError("Local video did not finish")


try:
    tasks.start()
    window.show()
    window.open_project(p)
    window.navigate(8)
    wait(lambda: window.generate_video.isVisible() and window.generate_video.isEnabled())
    window.grab().save(str(OUT / "before-video.png"))
    QTest.mouseClick(window.generate_video, Qt.MouseButton.LeftButton)
    check(tasks.busy(p), "Actual Generate video button queues production in the background")
    check(not tasks.submit_video(p, {}), "Duplicate video submissions for the same work are rejected")
    wait(lambda: not tasks.busy(p) and window.preview_video.isEnabled())
    value = VideoProduction(engine).latest(p)
    check(value and value["state"] == "SUCCEEDED" and value["current"], "Real local video is published for exactly the confirmed versions")
    result = value["result"]
    report["video"] = result
    report["project_id"] = p
    quality = result["quality"]
    check(quality["full_decode_passed"] and quality["video_codec"] == "h264" and quality["audio_codec"] == "aac", "Full H.264 video and AAC audio decode passes")
    a,b = map(int, source["project"]["settings"]["aspect"].split(":"))
    check(quality["width"]*b == quality["height"]*a and quality["fps"] == 30, "Actual video keeps the confirmed aspect and 30 fps")
    timeline = json.loads((Path(result["video_file"]).parent / "timeline.json").read_text(encoding="utf-8"))
    cues = timeline["captions"]
    spoken = "".join(c["text"] for c in cues)
    expected = "".join(s["narration"] for s in engine.latest(p,"storyboard",published=True)["shots"])
    check("".join(spoken.split()) == "".join(expected.split()), "Every confirmed narration character is represented in the subtitle/audio segments")
    check(all(0 <= c["start_ms"] < c["end_ms"] <= result["duration_ms"] for c in cues), "Every subtitle cue lies within actual speech/video duration")
    check(all(engine.step(p,s)["state"] == "SUCCEEDED" for s in ("visuals","audio","export")), "Full-video media stages complete only after actual media checks")
    check(engine.latest(p,'export',published=True)['scope']=='full_video', "Published video keeps full-video scope")
    check(upstream == {s:engine.step(p,s)["output_artifact_id"] for s in upstream}, "Existing topic, draft, review, final and storyboard are not regenerated or replaced")
    check(engine._one("SELECT COUNT(*) AS n FROM attempts WHERE provider!='local'")["n"] == model_attempts_before, "No provider/model call was made")
    exported = window.export_to(OUT / "export-copy")
    check(exported and (exported/"video.mp4").is_file() and (exported/"captions.srt").is_file(), "Full-video export carries the real MP4 and subtitles with project documents")
    window.grab().save(str(OUT / "video-ready.png"))
    QTest.mouseClick(window.preview_video, Qt.MouseButton.LeftButton)
    preview = window.preview
    preview.audio.setVolume(0)
    displayed_frames = []
    preview.sink.videoFrameChanged.connect(lambda f: displayed_frames.append(f.startTime()) if f.isValid() else None)
    app.processEvents()
    QTest.mouseClick(preview.play_button, Qt.MouseButton.LeftButton)
    wait(lambda: preview.last_image is not None and preview.player.duration()>0, seconds=15)
    check(bool(preview.player.audioTracks()), "App preview recognizes the generated audio track")
    preview.player.setPosition(5000)
    wait(lambda: preview.player.position() >= 4500 and displayed_frames and displayed_frames[-1]>=4500000, seconds=8)
    app.processEvents()
    preview.grab().save(str(OUT / "video-preview.png"))
    check(preview.last_image.width() == quality["width"], "App preview decodes the actual produced video frame")
    preview.close()
    # Isolated database copy checks recovery/cancellation without changing the
    # delivered video's current version or touching the user's source project.
    recovery = OUT / "recovery-data"
    recovery.mkdir()
    with sqlite3.connect(recovery/"app.db") as db:
        engine.db.backup(db)
    for filename in ('video.mp4','captions.srt','narration.wav','cover.png'):
        target = recovery / value['relative_path'] / filename
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(Path(result['video_file']).parent/filename,target)
    probe = Engine(recovery, recover=False)
    source_artifacts = engine.root/"jobs"/engine.project(p)["job"]["id"]/"artifacts"
    shutil.copytree(source_artifacts, recovery/"jobs"/engine.project(p)["job"]["id"]/"artifacts")
    cancelled = Event(); cancelled.set()
    try:
        asyncio.run(VideoProduction(probe).run(p, {"quality":"720"}, cancelled))
    except WorkflowError as error:
        check(error.code == "CANCELLED", "Cancelled local render is not marked successful")
    latest = VideoProduction(probe).latest(p)
    check(latest["state"] == "CANCELLED" and latest.get("previous_result") and Path(latest['result']['video_file']).is_file(), "A failed/cancelled retry keeps the previous playable video")
    with probe.db:
        probe.db.execute("UPDATE steps SET state='STALE' WHERE job_id=? AND stage='storyboard'", (engine.project(p)["job"]["id"],))
    check(not VideoProduction(probe).latest(p)["current"], "Changed upstream content cannot masquerade as the current video")
    probe.close()
    report["status"] = "passed"
except Exception as error:
    report.update(status="failed",error=repr(error))
    window.grab().save(str(OUT/"failure.png"))
    raise
finally:
    tasks.shutdown()
    end = time.monotonic()+20
    while tasks.isRunning() and time.monotonic()<end:
        app.processEvents();time.sleep(.05)
    window.dirty.clear()
    window.close()
    report["evidence_dir"] = str(OUT)
    (OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (ROOT/"evidence/video-production-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":report["status"],"report":str(OUT/"report.json"),"video":report.get("video",{}).get("video_file")},ensure_ascii=False))
