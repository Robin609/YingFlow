"""Focused orchestration checks with explicit scripted test transports.

These checks prove state/validation behavior, not third-party connectivity.
Real provider probes are a separate entry point and evidence scope.
"""
import asyncio
import json
from pathlib import Path
import sys
from threading import Event
from uuid import uuid4

from video_app.core.contracts import ProviderError, TextResult
from video_app.core.engine import Engine
from video_app.core.pipeline import Pipeline
from video_app.providers.process import run_process

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "evidence" / ("pipeline-" + uuid4().hex[:10])
RUN.mkdir(parents=True)
checks = []


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    checks.append(description)


def document():
    return {"title": "ADC 抖动技术", "script": "用采样统计解释量化边界，分辨率不等于绝对精度。",
            "shots": [{"shot_id": "shot-001", "order": 1, "duration_ms": 40000,
                       "narration": "用采样统计解释量化边界，分辨率不等于绝对精度。",
                       "visual_prompt": "技术示意动画：量化台阶、采样点与寄存器位宽", "visual_type": "技术动画"}]}


class ScriptedProvider:
    def __init__(self, mode="valid"):
        self.calls = []
        self.mode = mode
        self.review_authorized = False

    async def generate(self, request, emit):
        self.calls.append(request.stage)
        self.last_request = request
        if self.mode == "auth_once" and len(self.calls) == 1:
            raise ProviderError("AUTH_REQUIRED", "测试登录前置条件")
        if self.mode == "review_auth" and request.stage == "review" and not self.review_authorized:
            raise ProviderError("AUTH_REQUIRED", "审核节点等待人工登录")
        emit({"kind": "dispatched", "dispatched": True})
        if self.mode == "temporary_once" and len(self.calls) == 1:
            raise ProviderError("PROVIDER_UNAVAILABLE", "测试503容量不足", dispatched=True, retryable=True)
        if self.mode == "timeout":
            raise ProviderError("PROCESS_TIMEOUT", "显式测试超时", dispatched=True)
        if self.mode == "late":
            engine.save_stage(request.project_id, "topic", {"title": "用户的新选题"}, confirm=True)
        if request.stage == "topic":
            payload = {"candidates": [{"candidate_id": "topic-1", "title": "ADC 抖动技术", "angle": "解释分辨率", "suggested_duration_ms": 40000}]}
        elif request.stage == "review":
            payload = {"verdict": "approve", "issues": [], "revised_document": document()}
            if self.mode == "blocking":
                payload.update(verdict="revise", issues=[{"problem": "测试阻塞问题", "fix": "需要修订", "severity": "error", "blocking": True}])
        else:
            payload = document()
        data = {"task_id": request.task_id, "run_id": request.run_id, "stage": request.stage,
                "upstream_sha256": request.upstream_sha256, "data": payload}
        if self.mode == "wrong_run":
            data["run_id"] = "wrong-run"
        if self.mode == "repair_once" and len(self.calls) == 1:
            return TextResult("broken json", {"test_only": True})
        return TextResult(json.dumps(data, ensure_ascii=False), {"test_only": True})


async def main():
    global engine
    engine = Engine(RUN / "data")
    brief = {"domain": "嵌入式", "audience": "开发者", "aspect": "4:3", "target_ms": 40000, "language": "中文"}
    project_id = engine.create_project("自动流程测试", brief)
    provider = ScriptedProvider()
    events = []
    pipeline = Pipeline(engine, events.append, {"antigravity": provider, "gemini_web": provider, "codex": provider})
    await pipeline.run(project_id, Event())
    check(provider.calls == ["topic"], "选题生成后停在人工选择点")
    check(engine.step(project_id, "topic")["state"] == "WAITING_USER", "候选没有伪装成已选择选题")
    await pipeline.run(project_id, Event())
    check(provider.calls == ["topic"], "等待选题期间不会重复调用模型")
    engine.save_stage(project_id, "topic", {"title": "ADC 抖动技术", "angle": "解释分辨率"}, confirm=True)
    await pipeline.run(project_id, Event())
    check(provider.calls == ["topic", "draft", "review"], "初稿与审核自动连续执行")
    check(engine.step(project_id, "draft")["state"] == "SUCCEEDED" and engine.step(project_id, "review")["state"] == "SUCCEEDED", "校验通过的正文才进入下游")
    check(engine.step(project_id, "final")["state"] == "WAITING_USER", "关闭GPT时生成本地终稿待确认版本")
    check(engine.versions(project_id, "draft")[0]["output_provider"] == "antigravity", "真实调用来源没有记作本地编辑")
    check(engine.versions(project_id, "final")[0]["output_provider"] == "local", "关闭GPT没有虚构Codex调用")
    final = engine.latest(project_id, "final")
    engine.save_stage(project_id, "final", final, confirm=True)
    await pipeline.run(project_id, Event())
    check(engine.latest(project_id, "storyboard")["shots"] == final["shots"], "终稿的分镜自动带入分镜工作区")
    engine.save_stage(project_id, "storyboard", engine.latest(project_id, "storyboard"), confirm=True)
    await pipeline.run(project_id, Event())
    check(engine.step(project_id, "export")["state"] == "WAITING_USER", "导出保留真实目录选择点")
    folder = engine.export_project(project_id, RUN / "exports")
    check((folder / "script.md").is_file(), "整个自动编排流程可实际导出")

    for mode, expected in [("wrong_run", "FAILED"), ("timeout", "UNKNOWN"), ("repair_once", "WAITING_USER")]:
        p = engine.create_project(mode, brief)
        fake = ScriptedProvider(mode)
        flow = Pipeline(engine, events.append, {"antigravity": fake})
        await flow.run(p, Event())
        check(engine.step(p, "topic")["state"] == expected, f"{mode} 明确进入 {expected}")
        if mode == "wrong_run":
            check(not engine.versions(p, "topic"), "错误run_id不能产生正式版本")
        elif mode == "timeout":
            await flow.run(p, Event(), retry=True)
            check(len(fake.calls) == 1, "UNKNOWN即使点击重试也不自动重发")
            request = fake.last_request
            data = {"task_id": request.task_id, "run_id": request.run_id, "stage": request.stage,
                    "upstream_sha256": request.upstream_sha256,
                    "data": {"candidates": [{"candidate_id": "recovered", "title": "已完成的原选题", "angle": "只读恢复", "suggested_duration_ms": 40000}]}}
            (request.evidence_dir / "generation.stdout.txt").write_text(json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": json.dumps(data, ensure_ascii=False)}}, ensure_ascii=False), encoding="utf-8")
            await flow.reconcile(p, Event())
            check(engine.step(p, "topic")["state"] == "WAITING_USER" and len(fake.calls) == 1,
                  "只读核对原完成事件可恢复结果，未创建新请求")
        else:
            check(len(fake.calls) == 2, "结构修复最多一次并创建新尝试")

    for mode in ("auth_once", "temporary_once"):
        p = engine.create_project(mode, brief)
        fake = ScriptedProvider(mode)
        flow = Pipeline(engine, events.append, {"antigravity": fake})
        await flow.run(p, Event())
        if mode == "auth_once":
            check(engine.step(p, "topic")["error_code"] == "AUTH_REQUIRED", "登录前阻塞不会冒充成功")
            await flow.run(p, Event())
            check(len(fake.calls) == 2 and engine.step(p, "topic")["error_code"] is None, "用户处理登录后可通过继续流程恢复")
        else:
            check(len(fake.calls) == 2 and engine.step(p, "topic")["state"] == "WAITING_USER", "明确503失败有界退避后恢复，不更换模型")

    p = engine.create_project("审核登录门控", brief)
    engine.save_stage(p, "topic", {"title": "测试选题"}, confirm=True)
    fake = ScriptedProvider("review_auth")
    flow = Pipeline(engine, events.append, {"antigravity": fake, "gemini_web": fake})
    await flow.run(p, Event())
    check(fake.calls.count("review") == 1, "审核节点等待登录时立即停止，不循环重试登录前置条件")
    check(engine.step(p, "review")["error_code"] == "AUTH_REQUIRED", "审核登录门控保留可处理错误")
    fake.review_authorized = True
    await flow.run(p, Event())
    check(fake.calls.count("draft") == 1 and fake.calls.count("review") == 2, "登录处理后从审核继续，不重跑有效初稿")

    p = engine.create_project("有界审核修订", brief)
    engine.save_stage(p, "topic", {"title": "测试选题"}, confirm=True)
    fake = ScriptedProvider("blocking")
    await Pipeline(engine, events.append, {"antigravity": fake, "gemini_web": fake}).run(p, Event())
    check(fake.calls.count("review") == 3 and fake.calls.count("draft") == 3, "阻塞审核最多触发两轮自动修订")
    check(engine.step(p, "review")["state"] == "WAITING_USER" and not engine.versions(p, "final"), "修订后仍阻塞时等待人工，不进入终稿")

    p = engine.create_project("迟到结果", brief)
    engine.save_stage(p, "topic", {"title": "原选题"}, confirm=True)
    fake = ScriptedProvider("late")
    await Pipeline(engine, events.append, {"antigravity": fake}).run(p, Event())
    check(not engine.versions(p, "draft"), "输入变化后的迟到结果不覆盖用户新版本")
    check(engine.step(p, "draft")["state"] != "SUCCEEDED", "迟到响应不会让失效步骤成功")

    cancel = Event()
    script = "import os,time; os.write(1,b'{\\\"text\\\":\\\"'); os.write(1,'中文'.encode()[:1]); time.sleep(.05); os.write(1,'中文'.encode()[1:]+b'\\\"}\\n'); os.write(2,b'x'*100000)"
    received = []
    result = await run_process([sys.executable, "-c", script], cwd=RUN, evidence=RUN / "process", label="utf8",
                               timeout_ms=3000, cancel=cancel, emit=lambda _: None, parse_jsonl=True, on_event=received.append)
    check(received == [{"text": "中文"}], "进程流跨块中文解码和JSON分帧正确")
    check(len(result.stderr) == 100000, "stdout与stderr并行读取，不因缓冲区塞满阻塞")
    try:
        await run_process([sys.executable, "-c", "import time; time.sleep(10)"], cwd=RUN, evidence=RUN / "process", label="timeout",
                          timeout_ms=200, cancel=Event(), emit=lambda _: None)
    except ProviderError as error:
        check(error.code == "PROCESS_TIMEOUT", "宿主超时会终止自己持有的进程")
    else:
        raise AssertionError("Timeout did not occur")
    engine.close()
    report = {"scope": "offline_orchestration_and_real_child_process", "status": "passed", "model_calls": 0,
              "checks": checks, "evidence": str(RUN)}
    (ROOT / "evidence/pipeline-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
