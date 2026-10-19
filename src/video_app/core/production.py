"""Version-bound local video production, independent of text-only exports."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys

from .daily import DailyRecommendations
from .engine import canonical, now, uid
from .workflow import WorkflowError
from ..providers.process import run_process
from ..render.speech import speech_options


class VideoProduction:
    def __init__(self, engine, emit=None):
        self.engine = engine
        self.emit = emit or (lambda event: None)

    def inputs(self, project_id):
        project = self.engine.project(project_id)
        if project["job"]["state"] == "CANCELLED":
            raise WorkflowError("JOB_CANCELLED", "请先恢复创作任务。")
        for stage in ("final", "storyboard"):
            if self.engine.step(project_id, stage)["state"] != "SUCCEEDED":
                raise WorkflowError("DEPENDENCY_NOT_READY", "请先确认终稿与分镜，再生成视频。")
        return {stage: self.engine.step(project_id, stage)["output_artifact_id"] for stage in ("final", "storyboard")}

    def visual_inputs(self, project_id, shots=None):
        if shots is None:
            shots = self.engine.latest(project_id, "storyboard", published=True)["shots"]
        assets = self.engine.media(project_id, "visuals")
        result = {}
        for shot in shots:
            kind = shot.get("visual_type", "技术动画")
            if kind == "技术动画":
                continue
            if kind != "导入素材":
                raise WorkflowError("VIDEO_SCENE_TYPE", "生成视频服务尚未接入。请为该镜头选择技术动画，或在素材页导入并应用本地画面。")
            asset = next((a for a in assets if a["logical_shot_id"] == shot["shot_id"] and a["relative_path"] == shot.get("asset_path")), None)
            if not asset:
                raise WorkflowError("VISUAL_NOT_BOUND", f"镜头 {shot['shot_id']} 尚未绑定素材，请在素材页导入并点击“用于此镜头”。")
            if asset["state"] == "STALE" or json.loads(asset["input_json"]) != self.engine._shot_input(project_id, shot):
                raise WorkflowError("MEDIA_STALE", f"镜头 {shot['shot_id']} 的素材输入已变化，请重新确认应用。")
            stat = self.engine.media_path(asset["id"]).stat()
            result[shot["shot_id"]] = {"asset_id": asset["id"], "relative_path": asset["relative_path"],
                                      "name": asset["original_name"], "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        return result

    def latest(self, project_id):
        value = self.engine._one("SELECT * FROM video_renders WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (project_id,))
        if value:
            value["result"] = json.loads(value["result_json"] or "null")
            shown = value
            if not value["result"]:
                earlier = self.engine._one("SELECT * FROM video_renders WHERE project_id=? AND state IN ('SUCCEEDED','STALE') AND result_json IS NOT NULL ORDER BY created_at DESC,id DESC LIMIT 1", (project_id,))
                if earlier:
                    shown = earlier
                    value["result"] = json.loads(earlier["result_json"])
                    value["previous_result"] = True
            if value["result"]:
                # Keep the data-directory-relative identity authoritative so a
                # restored data folder can preview its existing videos too.
                folder = self.engine.root / shown["relative_path"]
                for key, filename in (("video_file", "video.mp4"), ("subtitle_file", "captions.srt"),
                                      ("audio_file", "narration.wav"), ("cover_file", "cover.png")):
                    value["result"][key] = str(folder / filename)
            try:
                recipe = json.loads(shown["input_json"])
                project = self.engine.project(project_id)
                value["current"] = (recipe["versions"] == self.inputs(project_id) and recipe.get("title") == project["name"]
                                    and recipe["aspect"] == project["settings"]["aspect"]
                                    and recipe.get("visuals", {}) == self.visual_inputs(project_id))
            except (WorkflowError, OSError):
                value["current"] = False
            value["folder"] = str(self.engine.root / value["relative_path"])
        return value

    def _publish_full_video(self, project, result):
        # Only this checked renderer may publish media stages. Manual save_stage
        # still rejects attempts to mark unchecked imports as completed media.
        folder = Path(result["video_file"]).parent
        for stage, payload in (
            ("visuals", {"renderer": "local-technical-v2", "video_file": result["video_file"], "quality": result["quality"], "visual_assets": result.get("visual_assets", [])}),
            ("audio", {"audio_file": result["audio_file"], "voice": result["voice"], "speech": result.get("speech"), "sample_rate": 24000}),
            ("export", {**result, "scope": "full_video", "render_scope": result["scope"], "folder": str(folder),
                        "files": ["video.mp4", "captions.srt", "narration.wav", "render.json", "timeline.json"]}),
        ):
            step = self.engine.step(project["id"], stage)
            self.engine._require_dependencies(project["id"], stage)
            from .workflow import dependencies
            upstream = [self.engine.step(project["id"], key)["output_artifact_id"] for key in dependencies(stage, "full_video")]
            artifact_id = uid()
            relative = Path("jobs") / project["job"]["id"] / "artifacts" / (artifact_id + ".json")
            raw = canonical(payload)
            target = self.engine.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            run_id = self.engine._new_attempt(step, "local", "SUCCEEDED")
            self.engine.db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   (artifact_id, step["id"], run_id, stage, relative.as_posix(), hashlib.sha256(raw).hexdigest(), json.dumps(upstream), step["output_artifact_id"], 1, now()))
            self.engine.db.execute("UPDATE steps SET output_artifact_id=? WHERE id=?", (artifact_id, step["id"]))
        self.engine._job_state(project["job"]["id"])

    async def run(self, project_id, options, cancel):
        versions = self.inputs(project_id)
        project = self.engine.project(project_id)
        shots = self.engine.read_artifact(versions["storyboard"])["shots"]
        visuals = self.visual_inputs(project_id, shots)
        speech = speech_options(options)
        if speech["provider"] == "google" and options.get("google_confirmed") is not True:
            raise WorkflowError("SPEECH_CONFIRMATION", "请在 App 内确认本次 Google Cloud 配音调用后再生成。")
        recipe = {"renderer": "local-technical-v2", "versions": versions, "title": project["name"], "visuals": visuals,
                  "speech": speech,
                  "voice": options.get("voice") or "", "rate": int(options.get("rate", 1)),
                  "quality": options.get("quality", "1080"), "aspect": project["settings"]["aspect"]}
        if recipe["quality"] not in {"720", "1080"} or not -3 <= recipe["rate"] <= 4:
            raise WorkflowError("VIDEO_SETTINGS", "请选择有效的清晰度与配音语速。")
        render_id = uid()
        folder = DailyRecommendations(self.engine).work_folder(project_id) / ("video-" + render_id[:8])
        folder.mkdir(parents=True)
        relative = folder.relative_to(self.engine.root).as_posix()
        render_shots = [{**shot, **({"imported_visual": {**visuals[shot["shot_id"]],
                         "path": str(self.engine.media_path(visuals[shot["shot_id"]]["asset_id"]))}}
                         if shot["shot_id"] in visuals else {})} for shot in shots]
        manifest = {**recipe, "planned_ms": project["settings"]["target_ms"], "shots": render_shots, "output_dir": str(folder),
                    "google_confirmed": options.get("google_confirmed") is True}
        (folder / "input.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.engine.db:
            self.engine.db.execute("INSERT INTO video_renders VALUES (?,?,?,?,?,NULL,NULL,?,NULL)",
                                   (render_id, project_id, "RUNNING", canonical(recipe).decode(), relative, now()))
            self.engine._event(project["job"]["id"], "开始制作视频：技术动画／导入画面、中文配音与字幕。")

        def notify(**values):
            self.emit({"kind": "video", "project_id": project_id, "render_id": render_id, **values})

        notify(phase="started", percent=0, message="正在准备配音、字幕与分镜画面…")
        try:
            def progress(event):
                notify(**event)
            await run_process(render_command(folder / "input.json"),
                              cwd=folder, evidence=folder / "logs", label="video", timeout_ms=1800000,
                              cancel=cancel, emit=lambda _: None, parse_jsonl=True, on_event=progress)
            result = json.loads((folder / "render.json").read_text(encoding="utf-8"))
            checked = {entry["asset_id"] for entry in result.get("visual_assets", []) if entry.get("used_range_decode_passed")}
            if checked != {entry["asset_id"] for entry in visuals.values()}:
                raise WorkflowError("MEDIA_QC", "导入素材的使用区段未全部通过检查，未发布成片。")
            if cancel.is_set():
                raise WorkflowError("CANCELLED", "视频制作已取消。")
            with self.engine.db:
                self.engine.db.execute("BEGIN IMMEDIATE")
                try:
                    latest_project = self.engine.project(project_id)
                    current = (self.inputs(project_id) == versions and latest_project["name"] == recipe["title"]
                               and latest_project["settings"]["aspect"] == recipe["aspect"]
                               and self.visual_inputs(project_id) == visuals)
                except (WorkflowError, OSError):
                    current = False
                state = "SUCCEEDED" if current else "STALE"
                if current and project["job"]["kind"] == "full_video":
                    self._publish_full_video(project, result)
                if current:
                    for asset in visuals.values():
                        self.engine.db.execute("UPDATE media_assets SET state='USED' WHERE id=?", (asset["asset_id"],))
                self.engine.db.execute("UPDATE video_renders SET state=?,result_json=?,finished_at=? WHERE id=?",
                                       (state, json.dumps(result, ensure_ascii=False), now(), render_id))
                self.engine._event(project["job"]["id"], "视频制作完成" + ("，但文稿、分镜或素材已变化，成片需更新。" if not current else "，MP4 与字幕已保存。"))
            notify(phase="completed", percent=100, message="视频已生成，可在 App 内预览。" if current else "本轮视频已保存；上游版本已变化，请重新生成。", result=result, state=state)
            return result
        except Exception as error:
            state = "CANCELLED" if cancel.is_set() or getattr(error, "code", "") == "CANCELLED" else "FAILED"
            with self.engine.db:
                self.engine.db.execute("UPDATE video_renders SET state=?,error=?,finished_at=? WHERE id=?", (state, str(error), now(), render_id))
            notify(phase="error", message=str(error), state=state)
            raise


def render_command(manifest):
    runtime = Path(sys.executable)
    if getattr(sys, "frozen", False):
        # The desktop EXE has no stdout. Keep JSONL progress on a separate
        # console-enabled worker, launched with CREATE_NO_WINDOW by transport.
        worker = runtime.with_name("VideoWorkbenchWorker.exe")
        if not worker.is_file():
            raise WorkflowError("VIDEO_RUNTIME", "视频组件不完整，请重新解压整个 App 文件夹，不要只复制主程序。")
        return [str(worker), "--render-worker", str(manifest)]
    if runtime.name.lower() == "pythonw.exe":
        runtime = runtime.with_name("python.exe")
    return [str(runtime), "-m", "video_app.render.worker", str(manifest)]
