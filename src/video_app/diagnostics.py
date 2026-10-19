"""Opt-in packaged acceptance in a new folder; no accounts or online TTS used."""
from pathlib import Path
import asyncio
import json
import sys
import time


def run_check(output):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton
    from .ui.window import Window, run_app
    from .ui.video_options import VideoOptionsDialog
    from .core.production import VideoProduction

    output = Path(output).resolve()
    if output.exists():
        raise ValueError("诊断仅允许使用全新目录，不能覆盖现有作品。")
    output.mkdir(parents=True)
    app = QApplication([])
    checks, errors = [], []
    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
              "checks": checks, "online_tts_requests": 0, "model_requests": 0}

    def wait(predicate, seconds=120):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            app.processEvents()
            if predicate():
                return
            time.sleep(.03)
        raise TimeoutError("诊断操作未在预算内完成。")

    def save_voice():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, VideoOptionsDialog) and dialog.windowTitle() == "配音设置"
            dialog.provider.setCurrentIndex(dialog.provider.findData("edge"))
            dialog.edge_voice.setCurrentIndex(dialog.edge_voice.findData("zh-CN-YunxiNeural"))
            dialog.edge_rate.setValue(15)
            dialog.edge_pitch.setValue(-2)
            app.processEvents()
            dialog.grab().save(str(output/"voice-options.png"))
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        except Exception as error:
            errors.append(repr(error))
            if dialog:
                dialog.reject()

    async def driver():
        from playwright.async_api import async_playwright
        instance = await async_playwright().start()
        await instance.stop()

    def inspect():
        window = next(w for w in app.topLevelWidgets() if isinstance(w, Window))
        try:
            import edge_tts
            import google.auth
            report["qt_platform"] = app.platformName()
            checks.append("Bundled Edge and Google speech modules import without reading credentials or generating audio")
            window.navigate(7)
            app.processEvents()
            window.grab().save(str(output/"settings.png"))
            QTimer.singleShot(80, save_voice)
            window.findChild(QPushButton, "speechSettings").click()
            assert not errors, errors
            values = window.engine.get_settings()["video_options"]
            assert values["edge_voice"] == "zh-CN-YunxiNeural" and values["edge_rate"] == 15 and values["edge_pitch"] == -2
            checks.append("Packaged desktop opens the real voice dialog and persists chosen voice parameters")
            dialog = VideoOptionsDialog(values, window, speech_only=True)
            assert dialog.values()["speech_provider"] == "edge" and dialog.values()["edge_pitch"] == -2
            dialog.provider.setCurrentIndex(dialog.provider.findData("google"))
            dialog._preview_voice()
            preview = dialog.preview
            preview.audio.setVolume(0)
            preview.player.play()
            wait(lambda: preview.player.duration()>0 and preview.player.position()>500, 15)
            assert preview.player.audioTracks()
            preview.close()
            dialog.close()
            checks.append("Bundled historical voice sample plays without online synthesis")
            asyncio.run(driver())
            checks.append("Bundled Playwright Node driver starts and stops without opening a browser")
            project = window.engine.create_project("独立 App 成片验证", {"domain": "嵌入式", "audience": "开发者", "aspect": "4:3", "target_ms": 10000}, "full_video")
            shots = [{"shot_id": "s1", "order": 1, "duration_ms": 5000, "narration": "先看技术动画，再看导入画面。", "visual_prompt": "ADC 采样原理", "visual_type": "技术动画"},
                     {"shot_id": "s2", "order": 2, "duration_ms": 5000, "narration": "画面、旁白和字幕，在工作台内合成为视频。", "visual_prompt": "本地图片展示", "visual_type": "技术动画"}]
            document = {"title": "独立应用", "script": "".join(s["narration"] for s in shots)}
            for stage, payload in (("topic", {"title": "独立应用"}), ("draft", document), ("review", {**document, "verdict": "approve", "issues": []}), ("final", document), ("storyboard", {"shots": shots})):
                window.engine.save_stage(project, stage, payload, confirm=True)
            image = QImage(480, 320, QImage.Format.Format_RGB32)
            image.fill(QColor("#56dec5"))
            image.save(str(output/"picture.png"))
            asset = window.engine.import_media(project, "visuals", output/"picture.png", "s2")
            window.engine.bind_visual_asset(project, asset)
            # No network voice is invoked. Real video encoding uses system speech.
            window.engine.save_settings({"video_options": {"speech_provider": "windows", "quality": "720", "rate": 2, "voice": ""}})
            window.open_project(project)
            window.navigate(8)
            window.generate_video.click()
            assert window.tasks.busy(project)
            wait(lambda: not window.tasks.busy(project), 240)
            value = VideoProduction(window.engine).latest(project)
            assert value and value["state"] == "SUCCEEDED" and value["current"], value
            result = value["result"]
            assert result["quality"]["full_decode_passed"] and result["visual_assets"]
            report["video"] = result
            report["project_id"] = project
            checks.append("Actual packaged Generate button runs its console worker, Windows voice, imported picture, captions and FFmpeg full decode")
            window.grab().save(str(output/"video-ready.png"))
            wait(lambda: window.preview_video.isEnabled(), 8)
            window.preview_video.click()
            preview = window.preview
            preview.audio.setVolume(0)
            displayed = []
            preview.sink.videoFrameChanged.connect(lambda f: displayed.append(f.startTime()) if f.isValid() else None)
            preview.player.play()
            wait(lambda: displayed and preview.player.duration()>2000, 15)
            target = max(1000, preview.player.duration()-1500)
            preview.slider.sliderMoved.emit(target)
            wait(lambda: displayed[-1] >= (target-200)*1000, 10)
            preview.player.pause()
            preview.grab().save(str(output/"video-preview.png"))
            preview.close()
            exported = window.export_to(output/"export")
            assert exported and (exported/"video.mp4").is_file()
            checks.append("Packaged App plays/seeks the actual video and exports MP4 with subtitles")
            # Leave persisted selectable defaults for an independent reopen.
            window.engine.save_settings({"video_options": values})
            report["status"] = "passed"
        except Exception as error:
            report.update(status="failed", error=repr(error))
            window.grab().save(str(output/"failure.png"))
        finally:
            (output/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            window.dirty.clear()
            window.closing = True
            window.close()

    QTimer.singleShot(500, inspect)
    run_app(output/"data", auto_recommendations=False)
    return 0 if report.get("status") == "passed" else 1
