"""One real provider echo through product transports, without credential copies."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

from video_app.config import load_settings
from video_app.core.contracts import TextRequest, ProviderError
from video_app.providers.antigravity import AntigravityProvider
from video_app.providers.codex import CodexProvider
from video_app.providers.gemini_web import BrowserSession, GeminiProvider


async def run(args):
    root = args.data_root.resolve()
    settings = load_settings(args.settings)
    session = None
    run_id = "probe-" + uuid4().hex
    directory = root / "probes" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    expected = {"task_id": run_id, "run_id": run_id, "stage": "probe",
                "upstream_sha256": hashlib.sha256(b"provider-connectivity-v1").hexdigest(),
                "data": {"text": "中文引号\"与换行\n传输成功"}}
    output_format = ("只输出一个json代码块，块内为下面这个完全相同的JSON对象，保留引号与换行的JSON转义，不要块外说明：\n"
                     if args.provider == "gemini_web" else "只输出下面这个完全相同的JSON对象，不要代码围栏和说明：\n")
    prompt = "不要使用工具，不读取文件。" + output_format + json.dumps(expected, ensure_ascii=False)
    (directory / "request.txt").write_text(prompt, encoding="utf-8")
    model = settings["agy_model"] if args.provider == "antigravity" else settings["codex_model"] if args.provider == "codex" else None
    request = TextRequest(run_id, run_id, run_id, run_id, "probe", args.provider, model, expected["upstream_sha256"],
                          {}, (), "", prompt, directory, Event(), args.timeout * 1000)
    report = {"scope": "real_provider_echo", "provider": args.provider, "run_id": run_id, "status": "running"}

    def emit(event):
        if event.get("kind") == "reference":
            report["provider_ref"] = event["provider_ref"]
        elif event.get("kind") == "dispatched":
            report["dispatched"] = True
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        if args.provider == "antigravity":
            provider = AntigravityProvider(settings, root)
        elif args.provider == "codex":
            provider = CodexProvider(settings, root)
        else:
            session = BrowserSession(settings, root)
            provider = GeminiProvider(session)
        async with asyncio.timeout(args.timeout):
            result = await provider.generate(request, emit)
        if json.loads(result.raw_text) != expected:
            raise ValueError("中文正文或任务标识不匹配")
        report.update(status="passed", provider_ref=result.provider_ref, model_observed=result.model_observed)
    except Exception as error:
        report.update(status="failed", error_code=getattr(error, "code", type(error).__name__), message=str(error))
    finally:
        if session:
            await session.disconnect()
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(directory)}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["antigravity", "codex", "gemini_web"])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
