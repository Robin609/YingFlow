"""Real Qt/TaskService acceptance of optional GPT, reusing a reviewed project.

Calls real services. This operator confirms the generated document/storyboard
only for this acceptance run; production human confirmation is unchanged.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from video_app.config import load_settings
from video_app.core.engine import Engine
from video_app.ui.tasks import TaskService
from video_app.ui.theme import load_system_fonts
from video_app.ui.window import Window


def main(args):
    load_settings()  # Resolve Windows environment before constructing Qt.
    root = args.data_root.resolve()
    output = root / "live-ui-gpt.json"
    lock = QLockFile(str(root / "session.lock"))
    if not lock.tryLock(100):
        raise RuntimeError("该项目数据目录已有工作台打开，请先在原窗口保存并关闭。")
    app = QApplication([])
    app.setStyle("Fusion")
    load_system_fonts()
    engine = Engine(root)
    tasks = TaskService(root)
    window = Window(engine, tasks, auto_recommendations=False)
    p = args.project_id
    report = {"scope": "real_qt_taskservice_optional_gpt", "project_id": p,
              "generated_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "operator_choices": "enable Codex final; confirm generated final and storyboard; export", "checks": []}

    def check(condition, text):
        if not condition:
            raise AssertionError(text)
        report["checks"].append(text)

    def wait_until(predicate, seconds=230):
        started = time.monotonic()
        while time.monotonic() - started < seconds:
            app.processEvents()
            if predicate():
                return
            if not tasks.busy():
                failure = next((s for s in engine.steps(p) if s["error_code"]), None)
                if failure:
                    raise RuntimeError(f"{failure['stage']}: {failure['error_code']}")
            time.sleep(0.03)
        raise TimeoutError("界面等待超时，保留已有结果，不重复派发。")

    try:
        check(engine.step(p, "review")["state"] == "SUCCEEDED", "复用已通过的真实审核稿")
        check(not any(s["state"] == "RUNNING" for s in engine.steps(p)), "没有并发运行的旧请求")
        before = {s: engine.step(p, s)["output_artifact_id"] for s in ("topic", "draft", "review")}
        tasks.start()
        window.show()
        window.open_project(p)
        window.navigate(1)
        combo = window.findChild(QComboBox, "provider_final")
        combo.setCurrentIndex(combo.findData("codex"))
        check(engine.step(p, "final")["provider"] == "codex", "流程控件实际保存GPT节点设置")
        QTest.mouseClick(window.findChild(QPushButton, "advanceWorkflow"), Qt.MouseButton.LeftButton)
        wait_until(lambda: engine.step(p, "final")["state"] == "RUNNING")
        window.navigate(6)
        app.processEvents()
        check(window.stack.currentIndex() == 6, "真实CLI运行期间界面可切换到任务页")
        window.grab().save(str(root / "live-ui-running.png"))
        wait_until(lambda: engine.step(p, "final")["state"] == "WAITING_USER" and not tasks.busy())
        app.processEvents()
        check(window.stack.currentIndex() == 3 and window.doc_stage.currentData() == "final",
              "真实GPT结果自动进入终稿人工确认页")
        check(window.editor.toPlainText() == engine.latest(p, "final")["script"], "界面正文与真实GPT版本一致")
        generated = engine.versions(p, "final")[0]
        check(generated["output_provider"] == "codex", "待确认版本保存真实Codex来源")
        report["gpt_artifact_id"] = generated["id"]
        report["gpt_run_id"] = engine.step(p, "final")["active_run_id"]
        window.grab().save(str(root / "live-ui-final.png"))
        QTest.mouseClick(window.findChild(QPushButton, "confirmDocument"), Qt.MouseButton.LeftButton)
        wait_until(lambda: engine.step(p, "storyboard")["state"] == "WAITING_USER" and not tasks.busy())
        check(window.stack.currentIndex() == 4 and window.shot_table.rowCount() > 0, "确认终稿后自动带入完整分镜")
        QTest.mouseClick(window.findChild(QPushButton, "confirmShots"), Qt.MouseButton.LeftButton)
        wait_until(lambda: engine.step(p, "export")["state"] == "WAITING_USER" and not tasks.busy())
        check(window.stack.currentIndex() == 8, "确认分镜后自动进入导出页")
        folder = window.export_to(root / "exports")
        check(bool(folder) and all((folder / name).is_file() for name in ("script.md", "storyboard.json", "project.json")),
              "App导出真实文稿、分镜与项目记录")
        check(engine.project(p)["job"]["state"] == "SUCCEEDED", "文本项目完成状态与导出一致")
        check(before == {s: engine.step(p, s)["output_artifact_id"] for s in before}, "启用GPT未重跑选题、初稿或审核")
        report["export"] = str(folder)
        report["platform"] = app.platformName()
        report["steps"] = [{key: s[key] for key in ("stage", "state", "provider", "error_code")} for s in engine.steps(p)]
        window.navigate(1)
        app.processEvents()
        window.grab().save(str(root / "live-ui-completed.png"))
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", message=str(error), error_code=getattr(error, "code", type(error).__name__))
    finally:
        window.closing = True
        tasks.shutdown()
        until = time.monotonic() + 20
        while tasks.isRunning() and time.monotonic() < until:
            app.processEvents()
            time.sleep(0.03)
        if tasks.isRunning():
            report.update(status="failed", message="后台仍在停止，现场已保留。")
        else:
            window.close()
            app.processEvents()
        lock.unlock()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    raise SystemExit(main(parser.parse_args()))
