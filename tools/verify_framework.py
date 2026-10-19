"""Focused, offline acceptance of the actual desktop framework and storage.

Uses real Qt widgets and clicks. All text is explicit local test input; no model
or media-generation provider is called. Writes evidence only under evidence/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton, QTableWidgetItem

from video_app.core.engine import Engine
from video_app.core.workflow import WorkflowError
from video_app.ui.window import Window, ProjectDialog
from video_app.ui.theme import load_system_fonts


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
RUN = EVIDENCE / f"framework-{uuid4().hex[:10]}"
RUN.mkdir()
app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")
load_system_fonts()
checks = []


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    checks.append(description)


def click(widget):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    app.processEvents()


def click_text(window, text):
    control = next(b for b in window.findChildren(QPushButton) if b.text() == text and b.isVisible())
    click(control)


def snapshot(window, filename):
    app.processEvents()
    check(window.grab().save(str(RUN / filename)), f"截图保存：{filename}")


def error_code(callback, code):
    try:
        callback()
    except WorkflowError as error:
        check(error.code == code, f"拒绝路径：{code}")
    else:
        raise AssertionError(f"Expected {code}")


def run():
    engine = Engine(RUN / "data")
    window = Window(engine)
    window.show()
    window.navigate(0)
    QTest.qWait(80)
    snapshot(window, "01-empty-projects.png")

    def fill_dialog():
        dialog = next(w for w in app.topLevelWidgets() if isinstance(w, ProjectDialog) and w.isVisible())
        dialog.name.setText("ADC 抖动技术 · 框架验收")
        dialog.duration.setValue(20)
        actions = dialog.findChild(QDialogButtonBox)
        click(actions.button(QDialogButtonBox.StandardButton.Save))

    QTimer.singleShot(50, fill_dialog)
    click(window.findChild(QPushButton, "newProject"))
    project_id = window.project_id
    check(bool(project_id), "界面创建真实持久化项目")
    check(engine.step(project_id, "brief")["state"] == "SUCCEEDED", "项目要求形成已确认的本地版本")
    click(window.findChild(QPushButton, "advanceWorkflow"))
    before = len(engine.attempts(project_id))
    click(window.findChild(QPushButton, "advanceWorkflow"))
    check(engine.step(project_id, "topic")["state"] == "BLOCKED", "未接入生成服务时明确阻塞")
    check(before == len(engine.attempts(project_id)), "重复继续不创建重复尝试")
    check(not any(a["dispatched"] for a in engine.attempts(project_id)), "没有派发任何外部生成请求")
    snapshot(window, "02-workflow-blocked.png")

    window.open_stage("topic")
    window.topic_title.setText("给 ADC 加点噪声，为什么反而测得更细？")
    window.topic_angle.setPlainText("用采样点和寄存器动画解释过采样、抖动与有效分辨率。")
    click(window.findChild(QPushButton, "confirmTopic"))
    check(engine.step(project_id, "topic")["state"] == "SUCCEEDED", "人工确认选题并进入文稿页")
    script = "给 ADC 加入适量噪声，为什么可能测得更细？\n先观察量化边界，再比较多次采样后的统计结果。\n分辨率增加，并不意味着绝对精度同样提高。"
    window.document_title.setText("ADC 过采样与抖动")
    window.editor.setPlainText(script)
    click(window.findChild(QPushButton, "saveDocument"))
    check(engine.step(project_id, "draft")["state"] != "SUCCEEDED", "保存草稿不会发布到下游")
    click(window.findChild(QPushButton, "confirmDocument"))
    check(engine.step(project_id, "draft")["state"] == "SUCCEEDED", "确认初稿版本")

    window.doc_stage.setCurrentIndex(window.doc_stage.findData("review"))
    click_text(window, "使用上阶段正文")
    window.review_notes.setText("人工审核：区分分辨率与绝对精度；保留边界条件。")
    click(window.findChild(QPushButton, "confirmDocument"))
    check(engine.step(project_id, "review")["state"] == "SUCCEEDED", "人工审核包含修订正文并通过")
    window.doc_stage.setCurrentIndex(window.doc_stage.findData("final"))
    click_text(window, "使用上阶段正文")
    click(window.findChild(QPushButton, "confirmDocument"))
    check(engine.latest(project_id, "final", published=True)["script"] == script, "不调用 GPT 也可确认本地终稿")
    snapshot(window, "03-document-workbench.png")

    click(window.work_tab_buttons[4])
    click(window.findChild(QPushButton, "addShot"))
    window.shot_table.item(0, 1).setText("8")
    click(window.findChild(QPushButton, "addShot"))
    for r, narration, prompt in [(0, "先观察量化边界", "绘制电压曲线与量化台阶"),
                                  (1, "分辨率与精度不同", "对照采样统计与寄存器位宽")]:
        window.shot_table.item(r, 2).setText(narration)
        window.shot_table.item(r, 3).setText(prompt)
    window.shot_table.item(1, 1).setText("10")
    click(window.findChild(QPushButton, "confirmShots"))
    check(engine.step(project_id, "storyboard")["state"] != "SUCCEEDED", "总时长错误的分镜不能确认")
    window.shot_table.item(1, 1).setText("12")
    ids = [window.shot_table.item(r, 0).text() for r in range(2)]
    window.shot_table.selectRow(1)
    click_text(window, "上移")
    check(window.shot_table.item(0, 0).text() == ids[1], "分镜排序保留稳定镜头 ID")
    click(window.findChild(QPushButton, "confirmShots"))
    check(engine.step(project_id, "storyboard")["state"] == "SUCCEEDED", "分镜确认并保存旁白关联")
    snapshot(window, "04-storyboard.png")

    click(window.work_tab_buttons[5])
    source_video = ROOT.parents[3] / "embedded-trends-video/out/2026-10-18/给ADC加点噪声为什么反而测得更细与过采样抖动技术_4x3演示版.mp4"
    asset_id = window.import_media_from(source_video)
    check(bool(asset_id), "真实视频素材导入并关联稳定镜头 ID")
    check(engine.media_path(asset_id).stat().st_size == source_video.stat().st_size, "项目持有素材副本，原文件未被替换")
    check(engine.step(project_id, "visuals")["state"] == "SKIPPED", "导入素材不伪造媒体生成或验证完成")
    snapshot(window, "09-production.png")
    window.media_table.selectRow(0)
    click(window.findChild(QPushButton, "previewMedia"))
    preview = window.preview
    preview.audio.setMuted(True)
    preview.player.setPosition(15000)
    preview.player.play()
    for _ in range(200):
        app.processEvents()
        time.sleep(0.05)
        if preview.last_image is not None:
            break
    preview.player.pause()
    check(preview.last_image is not None, "App 内预览实际解码并显示视频帧")
    snapshot(preview, "10-media-preview.png")
    preview.close()

    window.navigate(1)
    snapshot(window, "05-workflow-ready.png")
    window.navigate(6)
    snapshot(window, "06-tasks.png")
    click(window.nav[7])
    click(window.findChild(QPushButton, "detectConnections"))
    for _ in range(800):
        app.processEvents()
        time.sleep(0.05)
        if window.worker and not window.worker.isRunning():
            break
    app.processEvents()
    check(not window.worker.isRunning(), "后台入口检测结束且界面可用")
    check('"generation_verified": false' in window.connection_report.toPlainText(), "入口检测不冒充真实生成验收")
    snapshot(window, "07-connections.png")

    click(window.work_tab_buttons[8])
    folder = window.export_to(RUN / "exports")
    check(folder and all((folder / f).is_file() for f in ["script.md", "storyboard.json", "project.json"]), "实际导出三个交付文件")
    check(engine.project(project_id)["job"]["state"] == "SUCCEEDED", "仅实际导出后文案任务完成")
    check(json.loads((folder / "storyboard.json").read_text(encoding="utf-8"))["shots"][0]["shot_id"] == ids[1], "导出使用当前已确认分镜顺序")
    snapshot(window, "08-export.png")
    window.close()
    app.processEvents()

    engine = Engine(RUN / "data")
    window = Window(engine)
    window.show()
    window.open_project(project_id)
    check(engine.project(project_id)["job"]["state"] == "SUCCEEDED", "重启恢复项目及完成状态")
    check(engine.latest(project_id, "final", published=True)["script"] == script, "重启后完整文稿保持一致")
    shot_payload = engine.latest(project_id, "storyboard", published=True)
    shot_payload["shots"][1]["visual_prompt"] += "；增加坐标说明"
    engine.save_stage(project_id, "storyboard", shot_payload, confirm=True)
    check(engine.media(project_id, "visuals")[0]["state"] == "IMPORTED", "修改其他镜头保留未变化镜头的素材")
    shot_payload["shots"][0]["visual_prompt"] += "；改变画面方案"
    engine.save_stage(project_id, "storyboard", shot_payload, confirm=True)
    check(engine.media(project_id, "visuals")[0]["state"] == "STALE", "对应画面输入变化使素材失效")
    old_final = engine.step(project_id, "final")["output_artifact_id"]
    error_code(lambda: engine.save_stage(project_id, "topic", {"title": "冲突"}, expected_latest="outdated"), "VERSION_CONFLICT")
    engine.save_stage(project_id, "topic", {"title": "调整后的 ADC 主题", "angle": "真实本地输入"}, confirm=True)
    check(all(engine.step(project_id, s)["state"] == "STALE" for s in ["draft", "review", "final", "storyboard", "export"]), "上游确认变更使全部相关下游失效")
    check(engine.read_artifact(old_final)["script"] == script, "旧版本不可变且可继续读取")
    error_code(lambda: engine.export_project(project_id, RUN / "bad-export"), "DEPENDENCY_NOT_READY")
    engine.cancel(project_id)
    check(engine.project(project_id)["job"]["state"] == "CANCELLED", "任务可取消且保留版本")
    error_code(lambda: engine.save_stage(project_id, "draft", {"script": "不应保存"}), "JOB_CANCELLED")
    engine.resume(project_id)
    check(engine.read_artifact(old_final)["script"] == script, "恢复任务不删除历史版本")

    other = engine.create_project("完整视频门控", {"domain": "技术", "audience": "开发者", "target_ms": 1000, "aspect": "4:3"}, "full_video")
    for stage, payload in [("topic", {"title": "测试"}), ("draft", {"script": "正文"}),
                           ("review", {"script": "正文", "verdict": "approve"}), ("final", {"script": "正文"}),
                           ("storyboard", {"shots": [{"shot_id": "a", "duration_ms": 1000, "narration": "正文", "visual_prompt": "动画"}]})]:
        engine.save_stage(other, stage, payload, confirm=True)
    check(engine.advance(other) == "visuals" and engine.step(other, "visuals")["state"] == "BLOCKED", "完整视频停在未接入画面服务")
    error_code(lambda: engine.export_project(other, RUN / "bad-video"), "DEPENDENCY_NOT_READY")

    step = engine.step(other, "visuals")
    with engine.db:
        engine.db.execute("UPDATE steps SET state='RUNNING' WHERE id=?", (step["id"],))
    window.close()
    app.processEvents()
    engine = Engine(RUN / "data")
    check(engine.step(other, "visuals")["state"] == "UNKNOWN", "崩溃恢复将未确认执行设为 UNKNOWN")
    error_code(lambda: engine.advance(other), "UNKNOWN_RESULT")
    engine.close()
    result = {"status": "passed", "scope": "desktop_framework_offline", "model_calls": 0,
              "checks": checks, "evidence_directory": str(RUN), "project_id": project_id,
              "screenshots": [str(p) for p in sorted(RUN.glob("*.png"))]}
    (EVIDENCE / "framework-acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
