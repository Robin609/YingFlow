"""Focused mixed-media acceptance: owned fixtures, actual UI/MP4, no model calls."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from threading import Event
import time
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import imageio_ffmpeg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from video_app.core.engine import Engine
from video_app.core.production import VideoProduction
from video_app.core.workflow import WorkflowError
from video_app.ui.tasks import TaskService
from video_app.ui.theme import load_system_fonts
from video_app.ui.window import Window

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence" / ("imported-visuals-" + uuid4().hex[:10])
OUT.mkdir(parents=True)
checks = []
report = {"checks": checks, "model_requests": 0, "scope": "short_local_mixed_media"}
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
app = QApplication([])
app.setStyle("Fusion")
load_system_fonts()
engine = Engine(OUT / "data")
tasks = TaskService(engine.root)
window = Window(engine, tasks, auto_recommendations=False)


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    checks.append(message)


def wait(predicate, seconds=180):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(.03)
    raise TimeoutError("Focused mixed-media action did not finish")


def command(args):
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))


def expect(code, call):
    try:
        call()
    except WorkflowError as error:
        check(error.code == code, "Explicitly blocked: " + code)
    else:
        raise AssertionError("Expected " + code)


def frame(video, ms, name):
    path = OUT / (name + ".png")
    command(["-ss", str(ms/1000), "-i", str(video), "-frames:v", "1", str(path)])
    value = QImage(str(path))
    check(not value.isNull(), "Extracted actual frame: " + name)
    return value


def close_color(a, b):
    return max(abs(a.red()-b.red()), abs(a.green()-b.green()), abs(a.blue()-b.blue())) < 12


try:
    # Deliberately distinctive portrait picture and a one-second clip with a
    # tone. Their source audio must never enter the narration track.
    picture = OUT / "portrait.png"
    image = QImage(240, 480, QImage.Format.Format_RGB32)
    image.fill(QColor("#ad26dd"))
    painter = QPainter(image)
    painter.setPen(QColor("white"))
    font = QFont("Arial", 19)
    painter.setFont(font)
    painter.drawText(image.rect(), int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter), "LOCAL IMAGE\n1 : 2")
    painter.end()
    image.save(str(picture))
    clip = OUT / "short-with-tone.mp4"
    command(["-f", "lavfi", "-i", "color=c=0x1da8b5:s=640x360:r=30:d=0.5",
             "-f", "lavfi", "-i", "color=c=0xf2b460:s=640x360:r=30:d=0.5",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=1:sample_rate=48000",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a:0",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)])
    brief = {"domain": "嵌入式原理", "audience": "开发者", "target_ms": 18000, "aspect": "4:3", "language": "中文"}
    p = engine.create_project("技术动画与本地素材混排", brief, "full_video")
    shots = [
        {"shot_id": "s1", "order": 1, "duration_ms": 6000, "narration": "先看采样原理，青线是信号，橙点是采样值。", "visual_prompt": "ADC 采样原理", "visual_type": "技术动画"},
        {"shot_id": "s2", "order": 2, "duration_ms": 6000, "narration": "再看导入图片，保持原图比例，不裁掉边缘。", "visual_prompt": "展示本地竖图", "visual_type": "技术动画"},
        {"shot_id": "s3", "order": 3, "duration_ms": 6000, "narration": "最后播放短片。片段结束后，画面停留在末帧。", "visual_prompt": "展示本地视频", "visual_type": "技术动画"},
    ]
    document = {"title": "素材混排验证", "script": "".join(s["narration"] for s in shots)}
    for stage, payload in (("topic", {"title": document["title"]}), ("draft", document),
                           ("review", {**document, "verdict": "approve", "issues": []}),
                           ("final", document), ("storyboard", {"shots": shots})):
        engine.save_stage(p, stage, payload, confirm=True)
    upstream = {stage: engine.step(p, stage)["output_artifact_id"] for stage in ("topic", "draft", "review", "final")}
    engine.save_settings({"video_options": {"quality": "720", "rate": 2, "voice": ""}})
    tasks.start()
    window.show()
    window.open_project(p)
    window.navigate(5)
    app.processEvents()
    storyboard = engine.step(p, "storyboard")["output_artifact_id"]
    window.media_shot.setCurrentIndex(window.media_shot.findData("s2"))
    a = window.import_media_from(picture)
    check(bool(a) and engine.step(p, "storyboard")["output_artifact_id"] == storyboard,
          "Original import entry only copies media; it does not silently publish a storyboard")
    QTest.mouseClick(window.findChild(QPushButton, "previewMedia"), Qt.MouseButton.LeftButton)
    wait(lambda: window.preview.last_image is not None, 5)
    check(not window.preview.last_image.isNull() and window.preview.last_image.height() == window.preview.last_image.width()*2,
          "App previews static pictures at their original aspect")
    window.preview.grab().save(str(OUT / "picture-preview.png"))
    window.preview.close()
    QTest.mouseClick(window.apply_visual, Qt.MouseButton.LeftButton)
    check(engine.step(p, "storyboard")["output_artifact_id"] != storyboard, "Actual Apply button publishes a new storyboard version")
    first_input = next(x for x in engine.media(p) if x["id"] == a)["input_json"]
    window.media_shot.setCurrentIndex(window.media_shot.findData("s3"))
    b = window.import_media_from(clip)
    QTest.mouseClick(window.apply_visual, Qt.MouseButton.LeftButton)
    check(next(x for x in engine.media(p) if x["id"] == a)["input_json"] == first_input,
          "Applying another shot does not invalidate unchanged shot media")
    check(set(VideoProduction(engine).visual_inputs(p)) == {"s2", "s3"}, "Both explicitly bound visual inputs are selected for rendering")
    window.grab().save(str(OUT / "applied-media.png"))
    window.navigate(8)
    app.processEvents()
    QTest.mouseClick(window.generate_video, Qt.MouseButton.LeftButton)
    check(tasks.busy(p), "Actual Generate button starts the mixed render in the background")
    wait(lambda: not tasks.busy(p))
    value = VideoProduction(engine).latest(p)
    check(value and value["state"] == "SUCCEEDED" and value["current"], "Mixed MP4 completes for the confirmed storyboard and exact media snapshots")
    result = value["result"]
    report.update(project_id=p, video=result)
    check(result["scope"] == "local_mixed_media" and result["quality"]["full_decode_passed"], "Full mixed MP4 video/audio decode passes")
    check({v["asset_id"] for v in result["visual_assets"]} == {a, b}, "Both imported assets have used-range decode evidence")
    video_report = next(v for v in result["visual_assets"] if v["asset_id"] == b)
    check(video_report["held_frames"] > 30 and video_report["source_audio"] == "muted", "Short video holds its last frame and its source audio is excluded")
    check(all(x["state"] == "USED" for x in engine.media(p)), "Assets become used only after successful render")
    board_id = engine.step(p, "storyboard")["output_artifact_id"]
    engine.bind_visual_asset(p, a)
    check(engine.step(p, "storyboard")["output_artifact_id"] == board_id, "Reapplying the same current asset is idempotent")
    check(upstream == {s: engine.step(p, s)["output_artifact_id"] for s in upstream}, "Text versions remain unchanged and no model stage is rerun")
    timeline = json.loads((Path(result["video_file"]).parent / "timeline.json").read_text(encoding="utf-8"))
    check("".join(c["text"] for c in timeline["captions"]) == document["script"], "Mixed media preserves every narration/subtitle character")
    starts = {s["shot_id"]: s["start_frame"]*1000/30 for s in timeline["shots"]}
    frame(result["video_file"], 800, "technical-frame")
    still = frame(result["video_file"], starts["s2"]+1200, "picture-frame")
    w, h = still.width(), still.height()
    color = QColor("#ad26dd")
    span = [x for x in range(w) if close_color(still.pixelColor(x, round(h*.6)), color)]
    check(125 <= len(span) <= 140 and close_color(still.pixelColor(w//2, round(h*.6)), color),
          "Actual encoded picture is visible, uncropped and 1:2 within the composition")
    early = frame(result["video_file"], starts["s3"]+100, "clip-first")
    late = frame(result["video_file"], starts["s3"]+2500, "clip-held")
    check(close_color(early.pixelColor(w//2, round(h*.6)), QColor("#1da8b5"))
          and close_color(late.pixelColor(w//2, round(h*.6)), QColor("#f2b460")),
          "Encoded clip advances from first to last frame and retains that last frame after source EOF")
    window.grab().save(str(OUT / "video-ready.png"))
    window.navigate(5)
    app.processEvents()
    check(all("已用于成片" in window.media_table.item(i,2).text() for i in range(2)), "App displays used media state after rendering")
    window.grab().save(str(OUT / "used-media.png"))
    exported = window.export_to(OUT / "export")
    check(exported and (exported/"video.mp4").is_file(), "Full-video export includes the real mixed MP4")

    # Only isolated copies are damaged for failure handling; the successful
    # example and original input files remain usable.
    recovery = OUT / "recovery-data"
    shutil.copytree(engine.root, recovery, ignore=shutil.ignore_patterns("app.db", "app.db-wal", "app.db-shm"))
    with sqlite3.connect(recovery / "app.db") as db:
        engine.db.backup(db)
    probe = Engine(recovery, recover=False)
    asset_path = probe.media_path(b)
    asset_path.write_bytes(b"intentionally invalid video fixture")
    check(not VideoProduction(probe).latest(p)["current"], "Externally changed media invalidates the previous video's current status")
    failed = False
    try:
        asyncio.run(VideoProduction(probe).run(p, {"quality": "720", "rate": 2}, Event()))
    except Exception:
        failed = True
    failed_value = VideoProduction(probe).latest(p)
    check(failed and failed_value["state"] == "FAILED" and failed_value.get("previous_result")
          and Path(failed_value["result"]["video_file"]).is_file(), "Bad clip fails explicitly while the previous playable MP4 remains available")
    changed = probe.latest(p, "storyboard", published=True)
    changed["shots"][1]["visual_prompt"] += "：修改画面方案"
    probe.save_stage(p, "storyboard", changed, confirm=True)
    expect("MEDIA_STALE", lambda: probe.bind_visual_asset(p, a))
    probe.bind_visual_asset(p, a, allow_stale=True)
    check(next(x for x in probe.media(p) if x["id"] == a)["state"] == "IMPORTED", "Stale assets can be reused only with explicit authorization")
    held = asset_path.with_suffix(".held")
    asset_path.rename(held)
    expect("MEDIA_PATH", lambda: VideoProduction(probe).visual_inputs(p))
    held.rename(asset_path)
    probe.close()
    check(picture.is_file() and clip.is_file(), "Original imported files remain unchanged and available")
    report["status"] = "passed"
except Exception as error:
    report.update(status="failed", error=repr(error))
    window.grab().save(str(OUT / "failure.png"))
    raise
finally:
    tasks.shutdown()
    end = time.monotonic() + 20
    while tasks.isRunning() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.05)
    window.dirty.clear()
    window.close()
    engine.close()
    report["evidence_dir"] = str(OUT)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "evidence/imported-visuals-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(OUT/"report.json"), "video": report.get("video", {}).get("video_file")}, ensure_ascii=False))
