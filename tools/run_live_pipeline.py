"""Run real product orchestration with explicit operator choices for acceptance.

Review provider is an explicit argument; a Codex review run does not prove the
default Gemini-web path. No production provider is silently substituted.
"""
import argparse
import asyncio
import json
from pathlib import Path
from threading import Event

from video_app.core.engine import Engine
from video_app.core.pipeline import Pipeline


async def main(args):
    engine = Engine(args.data_root.resolve())
    project_id = args.project_id
    if not project_id:
        project_id = engine.create_project("真实节点编排验收 · " + args.review_provider,
            {"domain": "嵌入式ADC采样技术", "audience": "嵌入式开发者", "language": "中文", "aspect": "4:3", "target_ms": 40000})
        engine.set_provider(project_id, "review", args.review_provider)
    output = args.data_root / "live-pipeline.json"
    report = {"scope": "real_text_pipeline", "project_id": project_id, "review_provider": args.review_provider,
              "operator_choices": "first generated topic; confirm generated final and storyboard for this acceptance run",
              "status": "running"}

    def persist():
        report["steps"] = [{"stage": s["stage"], "state": s["state"], "provider": s["provider"], "error_code": s["error_code"]} for s in engine.steps(project_id)]
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def event(value):
        if value.get("kind") not in {"text", "preview", "reference", "process"}:
            print(json.dumps(value, ensure_ascii=False), flush=True)
        persist()

    pipeline = Pipeline(engine, event)
    persist()
    try:
        await pipeline.run(project_id, Event(), retry=args.retry)
        topic = engine.latest(project_id, "topic") or {}
        if engine.step(project_id, "topic")["state"] == "WAITING_USER" and topic.get("candidates"):
            choice = topic["candidates"][0]
            engine.save_stage(project_id, "topic", {"title": choice["title"], "angle": choice["angle"], "candidate_id": choice["candidate_id"]}, confirm=True)
            await pipeline.run(project_id, Event())
        if engine.step(project_id, "final")["state"] == "WAITING_USER" and engine.latest(project_id, "final"):
            engine.save_stage(project_id, "final", engine.latest(project_id, "final"), confirm=True)
            await pipeline.run(project_id, Event())
        if engine.step(project_id, "storyboard")["state"] == "WAITING_USER" and engine.latest(project_id, "storyboard"):
            engine.save_stage(project_id, "storyboard", engine.latest(project_id, "storyboard"), confirm=True)
            await pipeline.run(project_id, Event())
        if engine.step(project_id, "export")["state"] == "WAITING_USER":
            folder = engine.export_project(project_id, args.data_root / "exports")
            report["export"] = str(folder)
        report["status"] = "passed" if engine.project(project_id)["job"]["state"] == "SUCCEEDED" else "incomplete"
    except Exception as error:
        report.update(status="failed", error_code=getattr(error, "code", type(error).__name__), message=str(error))
    finally:
        await pipeline.shutdown()
        persist()
        engine.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--review-provider", choices=["gemini_web", "codex"], default="gemini_web")
    parser.add_argument("--project-id")
    parser.add_argument("--retry", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
