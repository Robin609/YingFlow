"""Daily recommendations and automatic folders; workflow artifacts stay intact."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re

from .engine import Engine, now
from .workflow import WorkflowError


def today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


DEFAULT_BRIEF = {"domain": "嵌入式技术原理", "audience": "嵌入式开发者与电子爱好者",
                 "language": "中文", "target_ms": 230000, "aspect": "4:3"}


class DailyRecommendations:
    def __init__(self, engine: Engine):
        self.engine = engine

    def brief(self):
        return {**DEFAULT_BRIEF, **self.engine.get_settings().get("recommendation_brief", {})}

    def days(self):
        return self.engine._all("SELECT * FROM recommendation_days ORDER BY day DESC")

    def batch(self, project_id):
        return self.engine._one("SELECT * FROM recommendation_days WHERE project_id=?", (project_id,))

    def ensure(self, day=None):
        day = day or today()
        if date.fromisoformat(day).isoformat() != day:
            raise WorkflowError("RECOMMENDATION_DATE", "推荐日期格式不正确。")
        value = self.engine._one("SELECT * FROM recommendation_days WHERE day=?", (day,))
        folder = self.engine.root / "daily" / day
        folder.mkdir(parents=True, exist_ok=True)
        if not value:
            project_id = self.engine.create_project(day + " 每日推荐", self.brief())
            with self.engine.db:
                self.engine.db.execute("INSERT INTO recommendation_days VALUES (?,?,?,?)",
                                       (day, project_id, folder.relative_to(self.engine.root).as_posix(), now()))
            value = self.batch(project_id)
        return value

    def contents(self, day=None):
        batch = self.ensure(day)
        p = batch["project_id"]
        versions = self.engine.versions(p, "topic")
        artifact = versions[0] if versions else None
        brief_id = self.engine.step(p, "brief")["output_artifact_id"]
        # A changed recommendation direction must not display old suggestions
        # as if they were generated for the new brief.
        valid = artifact and json.loads(artifact["upstream_json"]) == [brief_id]
        payload = self.engine.read_artifact(artifact["id"]) if valid else {}
        return {**batch, "artifact_id": artifact["id"] if valid else None,
                "candidates": payload.get("candidates", []),
                "brief": self.engine.project(p)["settings"], "step": self.engine.step(p, "topic")}

    def sync(self, day=None):
        value = self.contents(day)
        folder = self.engine.root / value["relative_path"]
        record = {k: value[k] for k in ("day", "project_id", "artifact_id", "brief", "candidates")}
        text = json.dumps(record, ensure_ascii=False, indent=2)
        path = folder / "recommendations.json"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            part = path.with_suffix(".part")
            part.write_text(text, encoding="utf-8")
            part.replace(path)
        lines = [f"# {value['day']} 每日推荐", "", "内容方向：" + value["brief"]["domain"], ""]
        for candidate in value["candidates"]:
            lines += ["## " + candidate["title"], "", candidate["angle"], ""]
            if candidate.get("reason"):
                lines += ["推荐理由：" + candidate["reason"], ""]
            lines += ["- " + item for item in candidate.get("outline", [])]
            lines.append("")
        if not value["candidates"]:
            lines += ["尚未得到当前内容方向的推荐，请在 App 内生成或查看执行记录。", ""]
        path = folder / "推荐选题.md"
        rendered = "\n".join(lines)
        if not path.exists() or path.read_text(encoding="utf-8") != rendered:
            part = path.with_suffix(".part")
            part.write_text(rendered, encoding="utf-8")
            part.replace(path)
        return value

    def choose(self, day, candidate_id, *, expected_artifact_id=None):
        value = self.contents(day)
        if expected_artifact_id and value["artifact_id"] != expected_artifact_id:
            raise WorkflowError("RECOMMENDATION_CHANGED", "推荐内容已更新，请重新选择。")
        candidate = next((c for c in value["candidates"] if c["candidate_id"] == candidate_id), None)
        if not candidate:
            raise WorkflowError("RECOMMENDATION_CHANGED", "推荐内容已更新，请重新选择。")
        previous = self.engine._one("""SELECT project_id FROM recommendation_works
            WHERE day=? AND source_artifact_id=? AND candidate_id=?""", (day, value["artifact_id"], candidate_id))
        if previous:
            return previous["project_id"]
        brief = {**value["brief"], "target_ms": candidate["suggested_duration_ms"]}
        p = self.engine.create_project(candidate["title"], brief)
        self.engine.save_stage(p, "topic", {**candidate, "source_recommendation_artifact_id": value["artifact_id"]}, confirm=True)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", candidate["title"]).strip(" .")[:48] or "创作"
        relative = Path(value["relative_path"]) / (name + "-" + p[:8])
        folder = self.engine.root / relative
        folder.mkdir(parents=True, exist_ok=True)
        with self.engine.db:
            self.engine.db.execute("INSERT INTO recommendation_works VALUES (?,?,?,?,?,?)",
                                   (p, day, value["artifact_id"], candidate_id, relative.as_posix(), now()))
        (folder / "选题.json").write_text(json.dumps({"project_id": p, "day": day, "candidate": candidate,
            "source_artifact_id": value["artifact_id"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def work_folder(self, project_id):
        value = self.engine._one("SELECT relative_path FROM recommendation_works WHERE project_id=?", (project_id,))
        if value:
            folder = self.engine.root / value["relative_path"]
        else:
            project = self.engine.project(project_id)
            day = datetime.fromisoformat(project["created_at"]).astimezone(timezone(timedelta(hours=8))).date().isoformat()
            folder = self.engine.root / "daily" / day / ("创作-" + project_id[:8])
        folder.mkdir(parents=True, exist_ok=True)
        return folder
