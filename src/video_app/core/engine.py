"""SQLite-backed workflow with immutable local revisions and explicit gates."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
from uuid import uuid4

from .workflow import STAGES, STAGE_MAP, PROVIDERS, WorkflowError, applicable, dependencies


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid4().hex


def canonical(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


class Engine:
    def __init__(self, data_root: Path, *, recover=True):
        self.root = data_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "app.db")
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 4:
            self.db.close()
            raise WorkflowError("DATABASE_VERSION", "项目数据库来自更新的工作台版本，请使用相应版本打开。")
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, settings_json TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS steps (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                stage TEXT NOT NULL, position INTEGER NOT NULL, provider TEXT NOT NULL,
                state TEXT NOT NULL, active_run_id TEXT, output_artifact_id TEXT,
                error_code TEXT, updated_at TEXT NOT NULL, UNIQUE(job_id, stage));
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, step_id TEXT NOT NULL REFERENCES steps(id), ordinal INTEGER NOT NULL,
                provider TEXT NOT NULL, state TEXT NOT NULL, dispatched INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, started_at TEXT NOT NULL, finished_at TEXT, UNIQUE(step_id, ordinal));
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, step_id TEXT NOT NULL REFERENCES steps(id),
                run_id TEXT NOT NULL REFERENCES attempts(id), stage TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE, payload_sha256 TEXT NOT NULL,
                upstream_json TEXT NOT NULL, supersedes_id TEXT REFERENCES artifacts(id),
                confirmed INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
                step_id TEXT REFERENCES steps(id), run_id TEXT REFERENCES attempts(id),
                message TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS media_assets (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                kind TEXT NOT NULL, logical_shot_id TEXT, original_name TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE, input_json TEXT NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recommendation_days (
                day TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE REFERENCES projects(id),
                relative_path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recommendation_works (
                project_id TEXT PRIMARY KEY REFERENCES projects(id),
                day TEXT NOT NULL REFERENCES recommendation_days(day),
                source_artifact_id TEXT NOT NULL REFERENCES artifacts(id), candidate_id TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                UNIQUE(day,source_artifact_id,candidate_id));
            CREATE TABLE IF NOT EXISTS video_renders (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                state TEXT NOT NULL, input_json TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
                result_json TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(attempts)")}
        for column, declaration in {
            "input_manifest_json": "TEXT NOT NULL DEFAULT '{}'", "provider_ref_json": "TEXT",
            "model_requested": "TEXT", "model_observed": "TEXT", "request_sha256": "TEXT",
            "elapsed_ms": "INTEGER", "error_json": "TEXT", "owned_pid": "INTEGER",
        }.items():
            if column not in columns:
                self.db.execute(f"ALTER TABLE attempts ADD COLUMN {column} {declaration}")
        self.db.execute("PRAGMA user_version=4")
        self.db.commit()
        if recover:
            self._recover()

    def close(self):
        self.db.close()

    def _one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]

    def _event(self, job_id, message, step_id=None, run_id=None):
        self.db.execute("INSERT INTO events(job_id,step_id,run_id,message,created_at) VALUES (?,?,?,?,?)",
                        (job_id, step_id, run_id, message, now()))
        self.db.execute("UPDATE jobs SET updated_at=? WHERE id=?", (now(), job_id))
        self.db.execute("UPDATE projects SET updated_at=? WHERE id=(SELECT project_id FROM jobs WHERE id=?)",
                        (now(), job_id))

    def _recover(self):
        # No request is resent during recovery. Future dispatched adapters must
        # reconcile UNKNOWN against their own external reference.
        with self.db:
            self.db.execute("UPDATE video_renders SET state='INTERRUPTED',error='上次视频制作中断，已有文件保留，可重新生成。' WHERE state='RUNNING'")
            for step in self._all("SELECT * FROM steps WHERE state='RUNNING'"):
                self.db.execute("UPDATE steps SET state='UNKNOWN',error_code='RECOVERY_REQUIRED' WHERE id=?",
                                (step["id"],))
                self.db.execute("UPDATE attempts SET state='UNKNOWN' WHERE id=?", (step["active_run_id"],))
                self._event(step["job_id"], "上次执行未确认完成，需核对后继续。", step["id"], step["active_run_id"])
                self._job_state(step["job_id"])

    def list_projects(self, archived=False):
        return self._all("""SELECT p.*,j.id AS job_id,j.kind,j.state AS job_state FROM projects p
                            JOIN jobs j ON j.project_id=p.id WHERE p.archived=? ORDER BY p.updated_at DESC""",
                         (int(archived),))

    def project(self, project_id):
        result = self._one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not result:
            raise WorkflowError("PROJECT_NOT_FOUND", "项目不存在。")
        result["settings"] = json.loads(result.pop("settings_json"))
        result["job"] = self._one("SELECT * FROM jobs WHERE project_id=?", (project_id,))
        return result

    def steps(self, project_id):
        return self._all("SELECT s.* FROM steps s JOIN jobs j ON s.job_id=j.id WHERE j.project_id=? ORDER BY position",
                         (project_id,))

    def step(self, project_id, stage):
        return next(s for s in self.steps(project_id) if s["stage"] == stage)

    def create_project(self, name: str, settings: dict, kind="text_pipeline"):
        if not name.strip():
            raise WorkflowError("PROJECT_NAME", "请填写项目名称。")
        self._validate_brief(settings)
        if kind not in {"text_pipeline", "full_video"}:
            raise WorkflowError("JOB_KIND", "任务范围不正确。")
        project_id, job_id, timestamp = uid(), uid(), now()
        with self.db:
            self.db.execute("INSERT INTO projects(id,name,settings_json,created_at,updated_at) VALUES (?,?,?,?,?)",
                            (project_id, name.strip(), canonical(settings).decode(), timestamp, timestamp))
            self.db.execute("INSERT INTO jobs(id,project_id,kind,created_at,updated_at) VALUES (?,?,?,?,?)",
                            (job_id, project_id, kind, timestamp, timestamp))
            for i, stage in enumerate(STAGES):
                state = "PENDING" if applicable(stage.key, kind) else "SKIPPED"
                self.db.execute("INSERT INTO steps(id,job_id,stage,position,provider,state,updated_at) VALUES (?,?,?,?,?,?,?)",
                                (uid(), job_id, stage.key, i, stage.provider, state, timestamp))
            self._event(job_id, "创建项目；尚未调用任何生成服务。")
        self.save_stage(project_id, "brief", settings, confirm=True)
        return project_id

    @staticmethod
    def _validate_brief(data):
        if not str(data.get("domain", "")).strip() or not str(data.get("audience", "")).strip():
            raise WorkflowError("BRIEF_REQUIRED", "请填写领域与目标受众。")
        if type(data.get("target_ms")) is not int or not 1000 <= data["target_ms"] <= 3600000:
            raise WorkflowError("TARGET_DURATION", "目标时长应在 1 秒至 60 分钟之间。")
        if data.get("aspect") not in {"4:3", "16:9", "9:16", "1:1"}:
            raise WorkflowError("ASPECT", "请选择支持的画幅。")

    def update_project(self, project_id, name, settings):
        self._validate_brief(settings)
        if not name.strip():
            raise WorkflowError("PROJECT_NAME", "项目名称不能为空。")
        old = self.project(project_id)
        if old["settings"] != settings and old["job"]["state"] == "CANCELLED":
            raise WorkflowError("JOB_CANCELLED", "请先恢复任务，再修改项目要求。")
        with self.db:
            self.db.execute("UPDATE projects SET name=?,settings_json=?,updated_at=? WHERE id=?",
                            (name.strip(), canonical(settings).decode(), now(), project_id))
        if old["settings"] != settings:
            self.save_stage(project_id, "brief", settings, confirm=True)

    def archive_project(self, project_id, archived=True):
        with self.db:
            self.db.execute("UPDATE projects SET archived=?,updated_at=? WHERE id=?", (int(archived), now(), project_id))

    def _new_attempt(self, step, provider, state, error_code=None):
        run_id = uid()
        ordinal = self.db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM attempts WHERE step_id=?",
                                  (step["id"],)).fetchone()[0]
        self.db.execute("INSERT INTO attempts(id,step_id,ordinal,provider,state,error_code,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?)",
                        (run_id, step["id"], ordinal, provider, state, error_code, now(), None if state == "RUNNING" else now()))
        self.db.execute("UPDATE steps SET active_run_id=?,state=?,error_code=?,updated_at=? WHERE id=?",
                        (run_id, state, error_code, now(), step["id"]))
        return run_id

    def _require_dependencies(self, project_id, stage):
        job = self.project(project_id)["job"]
        for key in dependencies(stage, job["kind"]):
            if self.step(project_id, key)["state"] != "SUCCEEDED":
                raise WorkflowError("DEPENDENCY_NOT_READY", f"请先确认「{STAGE_MAP[key].title}」。")

    def _invalidate(self, project_id, changed_stage):
        job = self.project(project_id)["job"]
        affected = {changed_stage}
        for spec in STAGES:
            if spec.key == changed_stage or not affected.intersection(dependencies(spec.key, job["kind"])):
                continue
            affected.add(spec.key)
            step = self.step(project_id, spec.key)
            if step["state"] == "SKIPPED":
                continue
            state = "STALE" if step["output_artifact_id"] else "PENDING"
            self.db.execute("UPDATE steps SET state=?,active_run_id=NULL,error_code=NULL,updated_at=? WHERE id=?",
                            (state, now(), step["id"]))
        if changed_stage in {"brief", "topic", "draft", "review", "final"}:
            self.db.execute("UPDATE media_assets SET state='STALE' WHERE job_id=?", (job["id"],))
        elif changed_stage == "storyboard":
            current = self.latest(project_id, "storyboard", published=True) or {"shots": []}
            inputs = {shot["shot_id"]: self._shot_input(project_id, shot) for shot in current["shots"]}
            for asset in self.media(project_id, "visuals"):
                if inputs.get(asset["logical_shot_id"]) != json.loads(asset["input_json"]):
                    self.db.execute("UPDATE media_assets SET state='STALE' WHERE id=?", (asset["id"],))

    def _job_state(self, job_id):
        job = self._one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if job["state"] == "CANCELLED":
            return
        steps = self._all("SELECT * FROM steps WHERE job_id=?", (job_id,))
        states = {s["state"] for s in steps}
        state = next((s for s in ("UNKNOWN", "RUNNING", "BLOCKED", "WAITING_USER", "STALE", "FAILED") if s in states), "PENDING")
        if next(s for s in steps if s["stage"] == "export")["state"] == "SUCCEEDED":
            state = "SUCCEEDED"
        elif state == "PENDING" and any(s["stage"] != "brief" and s["state"] == "SUCCEEDED" for s in steps):
            state = "WAITING_USER"
        self.db.execute("UPDATE jobs SET state=?,updated_at=? WHERE id=?", (state, now(), job_id))

    def versions(self, project_id, stage):
        return self._all("""SELECT a.*,at.provider AS output_provider FROM artifacts a
                            JOIN attempts at ON a.run_id=at.id JOIN steps s ON a.step_id=s.id
                            JOIN jobs j ON s.job_id=j.id WHERE j.project_id=? AND a.stage=?
                            ORDER BY a.created_at DESC,a.rowid DESC""", (project_id, stage))

    def read_artifact(self, artifact_id):
        artifact = self._one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        if not artifact:
            raise WorkflowError("ARTIFACT_NOT_FOUND", "未找到该版本。")
        path = (self.root / artifact["relative_path"]).resolve()
        if not path.is_relative_to(self.root):
            raise WorkflowError("ARTIFACT_PATH", "版本路径不属于当前数据目录。")
        try:
            raw = path.read_bytes()
            # Payload integrity is part of the stored-version contract.
            if hashlib.sha256(raw).hexdigest() != artifact["payload_sha256"]:
                raise WorkflowError("ARTIFACT_CHANGED", "版本文件被外部修改，未使用该内容。")
            return json.loads(raw)
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowError("ARTIFACT_UNREADABLE", f"无法读取版本：{error}") from error

    def latest(self, project_id, stage, published=False):
        if published:
            artifact_id = self.step(project_id, stage)["output_artifact_id"]
            return self.read_artifact(artifact_id) if artifact_id else None
        versions = self.versions(project_id, stage)
        return self.read_artifact(versions[0]["id"]) if versions else None

    def _validate_payload(self, project_id, stage, payload):
        if stage == "brief":
            self._validate_brief(payload)
        elif stage == "topic":
            if not payload.get("title", "").strip():
                raise WorkflowError("TOPIC_REQUIRED", "请填写并确认选题标题。")
        elif stage in {"draft", "review", "final"}:
            if not payload.get("script", "").strip():
                raise WorkflowError("SCRIPT_REQUIRED", "正文不能为空。")
            if stage == "review" and not self.review_accepted(payload):
                raise WorkflowError("REVIEW_NOT_APPROVED", "审核仍有阻塞问题或已拒绝，请修订后重新审核。")
        elif stage == "storyboard":
            shots = payload.get("shots", [])
            if not shots:
                raise WorkflowError("SHOTS_REQUIRED", "请至少添加一个镜头。")
            ids = [s["shot_id"] for s in shots]
            if len(set(ids)) != len(ids):
                raise WorkflowError("SHOT_ID", "镜头 ID 重复。")
            for shot in shots:
                if type(shot["duration_ms"]) is not int or shot["duration_ms"] <= 0:
                    raise WorkflowError("SHOT_DURATION", "每个镜头的时长必须大于零。")
                if not shot.get("narration", "").strip() or not shot.get("visual_prompt", "").strip():
                    raise WorkflowError("SHOT_CONTENT", "每个镜头都需要旁白片段和画面方案。")
            target = self.project(project_id)["settings"]["target_ms"]
            if sum(s["duration_ms"] for s in shots) != target:
                raise WorkflowError("SHOT_TOTAL", f"镜头总时长需要等于项目目标 {target / 1000:g} 秒。")
        elif stage in {"visuals", "audio"}:
            raise WorkflowError("MEDIA_PROVIDER_NOT_CONFIGURED", "媒体适配器尚未接入；此阶段不能直接标为成功。")
        elif stage == "export":
            raise WorkflowError("USE_EXPORT", "请在导出页实际导出文件。")

    @staticmethod
    def review_accepted(payload):
        if any(issue.get("blocking", True) for issue in payload.get("issues", [])):
            return False
        return payload.get("verdict") == "approve" or (
            payload.get("verdict") == "revise" and isinstance(payload.get("revised_document"), dict)
            and not any(issue.get("blocking", True) for issue in payload.get("issues", [])))

    def save_stage(self, project_id, stage, payload, *, confirm=False, expected_latest=None,
                   source_run_id=None, expected_upstream=None):
        step = self.step(project_id, stage)
        if step["state"] == "SKIPPED":
            raise WorkflowError("STAGE_SKIPPED", "当前任务范围不需要这个阶段。")
        if self.project(project_id)["job"]["state"] == "CANCELLED":
            raise WorkflowError("JOB_CANCELLED", "请先在流程页恢复任务。")
        versions = self.versions(project_id, stage)
        latest_id = versions[0]["id"] if versions else ""
        if expected_latest is not None and expected_latest != latest_id:
            raise WorkflowError("VERSION_CONFLICT", "已有更新版本，请重新加载后再保存。")
        if confirm:
            self._require_dependencies(project_id, stage)
            self._validate_payload(project_id, stage, payload)
        raw = canonical(payload)
        digest = hashlib.sha256(raw).hexdigest()
        if not source_run_id and versions and versions[0]["payload_sha256"] == digest and bool(versions[0]["confirmed"]) == confirm:
            if not confirm or step["state"] == "SUCCEEDED":
                return versions[0]["id"]
        artifact_id = uid()
        relative = Path("jobs") / step["job_id"] / "artifacts" / f"{artifact_id}.json"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        part.write_bytes(raw)
        part.replace(target)
        upstream = [self.step(project_id, key)["output_artifact_id"]
                    for key in dependencies(stage, self.project(project_id)["job"]["kind"])]
        with self.db:
            if source_run_id:
                self.db.execute("BEGIN IMMEDIATE")
                self._assert_active(project_id, stage, source_run_id, expected_upstream)
            prior_state = step["state"]
            run_id = source_run_id or self._new_attempt(step, "local", "SUCCEEDED")
            if source_run_id:
                self.db.execute("UPDATE attempts SET state='SUCCEEDED',finished_at=? WHERE id=?", (now(), run_id))
            self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (artifact_id, step["id"], run_id, stage, relative.as_posix(), digest,
                             json.dumps(upstream), latest_id or None, int(confirm), now()))
            if confirm:
                self.db.execute("UPDATE steps SET state='SUCCEEDED',output_artifact_id=?,error_code=NULL WHERE id=?",
                                (artifact_id, step["id"]))
                self._invalidate(project_id, stage)
            else:
                # Saving a draft is not publishing it or invalidating downstream.
                state = prior_state if step["output_artifact_id"] and not source_run_id else "WAITING_USER"
                self.db.execute("UPDATE steps SET state=?,error_code=NULL,updated_at=? WHERE id=?", (state, now(), step["id"]))
            source = PROVIDERS[step["provider"]] if source_run_id else "本地人工编辑"
            self._event(step["job_id"], f"{'确认' if confirm else '保存待确认版本'}「{STAGE_MAP[stage].title}」；来源：{source}。",
                        step["id"], run_id)
            self._job_state(step["job_id"])
        return artifact_id

    def _assert_active(self, project_id, stage, run_id, upstream_ids=None):
        current = self.step(project_id, stage)
        if current["active_run_id"] != run_id or current["state"] != "RUNNING":
            raise WorkflowError("INPUT_VERSION_MISMATCH", "本轮已取消或被新版本取代，结果只保留为历史记录。")
        if self.project(project_id)["job"]["state"] == "CANCELLED":
            raise WorkflowError("JOB_CANCELLED", "任务已取消，本轮结果不再发布。")
        if upstream_ids is not None:
            ids = tuple(self.step(project_id, key)["output_artifact_id"]
                        for key in dependencies(stage, self.project(project_id)["job"]["kind"]))
            if ids != tuple(upstream_ids):
                raise WorkflowError("INPUT_VERSION_MISMATCH", "输入版本已变化，本轮结果不再发布。")

    def begin_run(self, project_id, stage, model=None, extra_input=None):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            project = self.project(project_id)
            if project["job"]["state"] == "CANCELLED":
                raise WorkflowError("JOB_CANCELLED", "请先恢复任务。")
            step = self.step(project_id, stage)
            if step["state"] in {"RUNNING", "UNKNOWN", "SKIPPED"}:
                raise WorkflowError("STEP_NOT_DISPATCHABLE", "此阶段正在执行、结果待核对或不在任务范围内。")
            self._require_dependencies(project_id, stage)
            ids = tuple(self.step(project_id, key)["output_artifact_id"] for key in dependencies(stage, project["job"]["kind"]))
            versions = self.versions(project_id, stage)
            payload = {"brief": project["settings"], "inputs": {key: self.latest(project_id, key, published=True)
                       for key in dependencies(stage, project["job"]["kind"])}}
            if extra_input:
                payload["correction"] = extra_input
            run_id = self._new_attempt(step, step["provider"], "RUNNING")
            manifest = {"upstream_artifact_ids": ids, "input_payload": payload,
                        "expected_latest": versions[0]["id"] if versions else ""}
            self.db.execute("UPDATE attempts SET input_manifest_json=?,model_requested=? WHERE id=?",
                            (canonical(manifest).decode(), model, run_id))
            self._event(step["job_id"], f"开始「{STAGE_MAP[stage].title}」：{PROVIDERS[step['provider']]}。", step["id"], run_id)
            self._job_state(step["job_id"])
        return {**manifest, "run_id": run_id, "step_id": step["id"], "task_id": step["job_id"], "provider": step["provider"]}

    def run_progress(self, run_id, *, dispatched=None, provider_ref=None, pid=None, message=None):
        attempt = self._one("SELECT a.*,s.job_id FROM attempts a JOIN steps s ON a.step_id=s.id WHERE a.id=?", (run_id,))
        if not attempt:
            return
        with self.db:
            if dispatched is not None:
                self.db.execute("UPDATE attempts SET dispatched=? WHERE id=?", (int(dispatched), run_id))
            if provider_ref is not None:
                self.db.execute("UPDATE attempts SET provider_ref_json=? WHERE id=?", (json.dumps(provider_ref, ensure_ascii=False), run_id))
            if pid is not None:
                self.db.execute("UPDATE attempts SET owned_pid=? WHERE id=?", (pid, run_id))
            if message:
                self._event(attempt["job_id"], message, attempt["step_id"], run_id)

    def finish_run(self, run_id, state, code, message, *, elapsed_ms=None, model_observed=None):
        attempt = self._one("SELECT a.*,s.job_id,s.active_run_id FROM attempts a JOIN steps s ON a.step_id=s.id WHERE a.id=?", (run_id,))
        if not attempt:
            return
        with self.db:
            self.db.execute("UPDATE attempts SET state=?,error_code=?,error_json=?,elapsed_ms=?,model_observed=?,finished_at=? WHERE id=?",
                            (state, code, json.dumps({"code": code, "message": message}, ensure_ascii=False), elapsed_ms, model_observed, now(), run_id))
            job = self._one("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],))
            if attempt["active_run_id"] == run_id and job["state"] != "CANCELLED":
                self.db.execute("UPDATE steps SET state=?,error_code=?,updated_at=? WHERE id=?", (state, code, now(), attempt["step_id"]))
            self._event(attempt["job_id"], message, attempt["step_id"], run_id)
            self._job_state(attempt["job_id"])

    def require_user(self, project_id, stage, message):
        step = self.step(project_id, stage)
        with self.db:
            self.db.execute("UPDATE steps SET state='WAITING_USER',error_code=NULL WHERE id=?", (step["id"],))
            self._event(step["job_id"], message, step["id"])
            self._job_state(step["job_id"])

    def authorize_redo_unknown(self, project_id):
        step = next((s for s in self.steps(project_id) if s["state"] == "UNKNOWN"), None)
        if not step:
            raise WorkflowError("NO_UNKNOWN_RUN", "没有结果待核对的阶段。")
        with self.db:
            self.db.execute("UPDATE steps SET state='PENDING',active_run_id=NULL,error_code=NULL WHERE id=?", (step["id"],))
            self._event(step["job_id"], "用户明确确认重做未知请求；原执行仍保留为 UNKNOWN，未冒充已取消。", step["id"], step["active_run_id"])
            self._job_state(step["job_id"])

    def set_provider(self, project_id, stage, provider):
        if provider not in PROVIDERS:
            raise WorkflowError("PROVIDER", "不支持的提供方。")
        step = self.step(project_id, stage)
        if step["provider"] == provider:
            return
        with self.db:
            self.db.execute("UPDATE steps SET provider=?,state=?,error_code=NULL,active_run_id=NULL WHERE id=?",
                            (provider, "STALE" if step["output_artifact_id"] else "PENDING", step["id"]))
            self._invalidate(project_id, stage)
            self._event(step["job_id"], f"「{STAGE_MAP[stage].title}」提供方设为 {PROVIDERS[provider]}。", step["id"])
            self._job_state(step["job_id"])

    def advance(self, project_id, retry=False):
        project = self.project(project_id)
        if project["job"]["state"] == "CANCELLED":
            raise WorkflowError("JOB_CANCELLED", "请先恢复任务。")
        for step in self.steps(project_id):
            if step["state"] in {"SUCCEEDED", "SKIPPED"}:
                continue
            if step["state"] == "UNKNOWN":
                raise WorkflowError("UNKNOWN_RESULT", "上一轮结果仍待核对，不能自动重发。")
            self._require_dependencies(project_id, step["stage"])
            state = "WAITING_USER" if step["provider"] == "local" else "BLOCKED"
            if step["state"] == state and not retry:
                return step["stage"]
            code = None if state == "WAITING_USER" else "PROVIDER_NOT_INTEGRATED"
            with self.db:
                run_id = self._new_attempt(step, step["provider"], state, code)
                message = "请在对应页面填写并确认内容。" if state == "WAITING_USER" else "适配器尚未接入；未派发外部请求。可使用本地编辑继续框架流程。"
                self._event(step["job_id"], f"「{STAGE_MAP[step['stage']].title}」{message}", step["id"], run_id)
                self._job_state(step["job_id"])
            return step["stage"]
        return None

    def cancel(self, project_id):
        job = self.project(project_id)["job"]
        if job["state"] == "SUCCEEDED":
            raise WorkflowError("JOB_ALREADY_COMPLETE", "任务已完成，不需要取消。")
        job_id = job["id"]
        with self.db:
            self.db.execute("UPDATE jobs SET state='CANCELLED' WHERE id=?", (job_id,))
            unknown = self._all("SELECT s.id,s.active_run_id FROM steps s JOIN attempts a ON s.active_run_id=a.id WHERE s.job_id=? AND s.state='RUNNING' AND a.dispatched=1", (job_id,))
            self.db.execute("UPDATE steps SET state='CANCELLED' WHERE job_id=? AND state IN ('PENDING','BLOCKED','WAITING_USER','RUNNING')", (job_id,))
            self.db.execute("UPDATE attempts SET state='CANCELLED',finished_at=? WHERE step_id IN (SELECT id FROM steps WHERE job_id=?) AND state IN ('WAITING_USER','BLOCKED','RUNNING')", (now(), job_id))
            for step in unknown:
                self.db.execute("UPDATE steps SET state='UNKNOWN',error_code='EXTERNAL_CANCEL_UNCONFIRMED' WHERE id=?", (step["id"],))
                self.db.execute("UPDATE attempts SET state='UNKNOWN' WHERE id=?", (step["active_run_id"],))
            self._event(job_id, "任务已取消；已有文稿和分镜版本仍保留，没有发出外部取消请求。")

    def resume(self, project_id):
        job = self.project(project_id)["job"]
        if job["state"] != "CANCELLED":
            return
        job_id = job["id"]
        with self.db:
            self.db.execute("UPDATE jobs SET state='PENDING' WHERE id=?", (job_id,))
            for step in self._all("SELECT * FROM steps WHERE job_id=? AND state='CANCELLED'", (job_id,)):
                state = "STALE" if step["output_artifact_id"] else "WAITING_USER" if self.versions(project_id, step["stage"]) else "PENDING"
                self.db.execute("UPDATE steps SET state=? WHERE id=?", (state, step["id"]))
            self._event(job_id, "恢复任务；未重发任何外部请求。")
            self._job_state(job_id)

    def events(self, project_id):
        job_id = self.project(project_id)["job"]["id"]
        return self._all("SELECT * FROM events WHERE job_id=? ORDER BY seq DESC LIMIT 200", (job_id,))

    def attempts(self, project_id):
        job_id = self.project(project_id)["job"]["id"]
        return self._all("SELECT a.*,s.stage FROM attempts a JOIN steps s ON a.step_id=s.id WHERE s.job_id=? ORDER BY a.started_at DESC", (job_id,))

    def get_settings(self):
        return {row["key"]: json.loads(row["value_json"]) for row in self._all("SELECT * FROM settings")}

    def save_settings(self, settings):
        with self.db:
            for key, value in settings.items():
                self.db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, json.dumps(value, ensure_ascii=False)))

    def media(self, project_id, kind=None):
        job_id = self.project(project_id)["job"]["id"]
        if kind:
            return self._all("SELECT * FROM media_assets WHERE job_id=? AND kind=? ORDER BY created_at DESC", (job_id, kind))
        return self._all("SELECT * FROM media_assets WHERE job_id=? ORDER BY created_at DESC", (job_id,))

    def _shot_input(self, project_id, shot):
        return {"shot_id": shot["shot_id"], "duration_ms": shot["duration_ms"],
                "visual_prompt": shot.get("visual_prompt", ""), "visual_type": shot.get("visual_type", ""),
                "asset_path": shot.get("asset_path", ""), "aspect": self.project(project_id)["settings"]["aspect"]}

    def import_media(self, project_id, kind: str, source: Path, shot_id=None):
        project = self.project(project_id)
        if project["job"]["state"] == "CANCELLED":
            raise WorkflowError("JOB_CANCELLED", "请先恢复任务。")
        if kind not in {"visuals", "audio"}:
            raise WorkflowError("MEDIA_KIND", "请选择画面或配音。")
        self._require_dependencies(project_id, kind)
        if not source.is_file() or source.stat().st_size == 0:
            raise WorkflowError("MEDIA_FILE", "请选择存在且非空的素材文件。")
        if kind == "visuals":
            shots = self.latest(project_id, "storyboard", published=True)["shots"]
            shot = next((s for s in shots if s["shot_id"] == shot_id), None)
            if shot is None:
                raise WorkflowError("SHOT_REQUIRED", "请选择当前分镜中的一个镜头。")
            input_data = self._shot_input(project_id, shot)
        else:
            input_data = {"final_artifact_id": self.step(project_id, "final")["output_artifact_id"]}
        asset_id = uid()
        relative = Path("jobs") / project["job"]["id"] / "media" / f"{asset_id}{source.suffix.lower()}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target.with_suffix(target.suffix + ".part"))
        target.with_suffix(target.suffix + ".part").replace(target)
        with self.db:
            self.db.execute("INSERT INTO media_assets VALUES (?,?,?,?,?,?,?,?,?)",
                            (asset_id, project["job"]["id"], kind, shot_id if kind == "visuals" else None,
                             source.name, relative.as_posix(), canonical(input_data).decode(), "IMPORTED", now()))
            self._event(project["job"]["id"], f"导入{'画面' if kind == 'visuals' else '配音'}素材：{source.name}；仅登记，待媒体验证。")
        return asset_id

    def media_path(self, asset_id):
        asset = self._one("SELECT * FROM media_assets WHERE id=?", (asset_id,))
        if not asset:
            raise WorkflowError("MEDIA_NOT_FOUND", "未找到素材记录。")
        target = (self.root / asset["relative_path"]).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise WorkflowError("MEDIA_PATH", "素材文件不在数据目录内或已经丢失。")
        return target

    def bind_visual_asset(self, project_id, asset_id, *, allow_stale=False):
        asset = self._one("SELECT * FROM media_assets WHERE id=?", (asset_id,))
        project = self.project(project_id)
        if project["job"]["state"] == "CANCELLED":
            raise WorkflowError("JOB_CANCELLED", "请先恢复任务。")
        if not asset or asset["job_id"] != project["job"]["id"] or asset["kind"] != "visuals":
            raise WorkflowError("MEDIA_KIND", "请选择这次创作的画面素材。")
        self.media_path(asset_id)
        if asset["state"] == "STALE" and not allow_stale:
            raise WorkflowError("MEDIA_STALE", "该素材对应旧画面方案，请明确确认后再用于当前镜头。")
        self._require_dependencies(project_id, "visuals")
        versions = self.versions(project_id, "storyboard")
        if not versions or versions[0]["id"] != self.step(project_id, "storyboard")["output_artifact_id"]:
            raise WorkflowError("UNCONFIRMED_STORYBOARD", "请先保存并确认当前分镜，避免覆盖尚未确认的编辑。")
        payload = self.read_artifact(versions[0]["id"])
        shot = next((s for s in payload["shots"] if s["shot_id"] == asset["logical_shot_id"]), None)
        if shot is None:
            raise WorkflowError("SHOT_REQUIRED", "原镜头已不存在，请重新选择镜头导入素材。")
        if (shot.get("visual_type") == "导入素材" and shot.get("asset_path") == asset["relative_path"]
                and asset["state"] != "STALE" and json.loads(asset["input_json"]) == self._shot_input(project_id, shot)):
            return shot["shot_id"]
        shot.update(visual_type="导入素材", asset_path=asset["relative_path"])
        # Binding is an explicit new storyboard version; old source files and
        # all prior scene versions remain available.
        self.save_stage(project_id, "storyboard", payload, confirm=True, expected_latest=versions[0]["id"])
        with self.db:
            self.db.execute("UPDATE media_assets SET input_json=?,state='IMPORTED' WHERE id=?",
                            (canonical(self._shot_input(project_id, shot)).decode(), asset_id))
            self._event(project["job"]["id"], f"画面素材已用于镜头 {shot['shot_id']}：{asset['original_name']}")
        return shot["shot_id"]

    def export_project(self, project_id, destination: Path):
        if self.project(project_id)["job"]["state"] == "CANCELLED":
            raise WorkflowError("JOB_CANCELLED", "请先恢复任务再导出。")
        self._require_dependencies(project_id, "export")
        project = self.project(project_id)
        video_result = None
        if project["job"]["kind"] == "full_video":
            from .production import VideoProduction
            video = VideoProduction(self).latest(project_id)
            if not video or not video["current"] or not video.get("result"):
                raise WorkflowError("MEDIA_NOT_READY", "请先生成当前确认版本的视频。")
            video_result = video["result"]
        final = self.latest(project_id, "final", published=True)
        shots = self.latest(project_id, "storyboard", published=True)
        step = self.step(project_id, "export")
        export_id = uid()
        folder = destination.resolve() / f"video-project-{export_id[:8]}"
        folder.mkdir(parents=True, exist_ok=False)
        stage_versions = {s.key: [{**v, "payload": self.read_artifact(v["id"])} for v in self.versions(project_id, s.key)] for s in STAGES}
        manifest = {"schema_version": "videoapp-project-v1", "project": project,
                    "steps": self.steps(project_id), "versions": stage_versions,
                    "attempts": self.attempts(project_id), "events": self.events(project_id),
                    "media_inventory": self.media(project_id), "media_files_included": False,
                    "generated_video_included": bool(video_result),
                    "scope": project["job"]["kind"], "exported_at": now()}
        files = {
            "project.json": json.dumps(manifest, ensure_ascii=False, indent=2),
            "storyboard.json": json.dumps(shots, ensure_ascii=False, indent=2),
            "script.md": f"# {final.get('title') or project['name']}\n\n{final['script']}\n",
        }
        for filename, text in files.items():
            part = folder / (filename + ".part")
            part.write_text(text, encoding="utf-8")
            part.replace(folder / filename)
        exported_files = list(files)
        if video_result:
            for key in ("video_file", "subtitle_file", "audio_file"):
                source = Path(video_result[key])
                shutil.copyfile(source, folder / source.name)
                exported_files.append(source.name)
        payload = {"folder": str(folder), "files": exported_files, "scope": project["job"]["kind"],
                   "final_artifact_id": self.step(project_id, "final")["output_artifact_id"],
                   "storyboard_artifact_id": self.step(project_id, "storyboard")["output_artifact_id"]}
        upstream_ids = [self.step(project_id, key)["output_artifact_id"] for key in dependencies("export", project["job"]["kind"])]
        relative = Path("jobs") / step["job_id"] / "artifacts" / f"{export_id}.json"
        raw = canonical(payload)
        (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
        path = self.root / relative
        path.with_suffix(".part").write_bytes(raw)
        path.with_suffix(".part").replace(path)
        with self.db:
            run_id = self._new_attempt(step, "local", "SUCCEEDED")
            self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (export_id, step["id"], run_id, "export", relative.as_posix(),
                             hashlib.sha256(raw).hexdigest(), json.dumps(upstream_ids),
                             step["output_artifact_id"], 1, now()))
            self.db.execute("UPDATE steps SET state='SUCCEEDED',output_artifact_id=? WHERE id=?", (export_id, step["id"]))
            self._event(step["job_id"], f"文稿与分镜已实际导出：{folder}", step["id"], run_id)
            self._job_state(step["job_id"])
        return folder
