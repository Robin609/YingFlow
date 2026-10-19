"""Isolated, subscription-backed Antigravity CLI text transport."""
from __future__ import annotations

import json
from pathlib import Path

from ..core.contracts import ProviderError, TextRequest, TextResult, provider_failure
from .process import run_process
from .base import TextTransport


MARKER = "video-app-isolated-config-marker"
AGENT = "video-app-text-only"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class AntigravityProvider(TextTransport):
    def __init__(self, settings: dict, data_root: Path):
        super().__init__()
        self.settings = settings
        self.root = data_root

    async def _generate(self, request: TextRequest, emit) -> TextResult:
        executable = self.settings.get("agy_executable")
        if not executable or not Path(executable).is_file():
            raise ProviderError("CLI_NOT_FOUND", "请在连接页指定 Antigravity CLI 路径。")
        runtime = self.root / "runtimes/agy" / request.run_id
        profile = runtime / "profile"
        workspace = runtime / "workspace"
        mcp = profile / ".gemini/config/mcp_config.json"
        env = {"USERPROFILE": str(profile), "HOME": str(profile)}
        write_json(mcp, {"mcpServers": {MARKER: {"command": "never-executed-listing-marker"}}})
        agent_path = profile / f".gemini/config/agents/{AGENT}.md"
        agent_path.parent.mkdir(parents=True, exist_ok=True)
        agent_path.write_text(f"---\nname: {AGENT}\ndescription: Text-only video workbench node\ntools: []\n---\n"
                              "Only return the requested JSON. Never use tools, read files, run commands or delegate.\n", encoding="utf-8")
        write_json(profile / ".gemini/antigravity-cli/settings.json", {"useG1Credits": False})
        write_json(profile / ".gemini/config/config.json", {"userSettings": {"useAiCredits": False}})

        async def probe(label, args, timeout=15000):
            return await run_process([executable, *args], cwd=workspace, evidence=request.evidence_dir,
                                     label=label, timeout_ms=timeout, cancel=request.cancel, emit=emit, env_overrides=env)

        version = (await probe("version", ["--version"])).stdout.strip()
        marked = (await probe("isolation-marker", ["mcp", "list"])).stdout
        if MARKER not in marked:
            raise ProviderError("CONFIG_ISOLATION_FAILED", "未观察到 App 的工作配置标记，未发起生成。")
        write_json(mcp, {"mcpServers": {}})
        empty = (await probe("isolation-empty", ["mcp", "list"])).stdout
        if MARKER in empty or not any(message in empty.lower() for message in ["no mcp", "no servers", "0 server", "没有", "未配置"]):
            raise ProviderError("CONFIG_ISOLATION_FAILED", "工作配置未确认为空 MCP 列表，未发起生成。")
        agents = (await probe("agents", ["agents"])).stdout
        if AGENT not in agents.split():
            raise ProviderError("CONFIG_ISOLATION_FAILED", "未发现文本任务配置。")
        models = (await probe("models", ["models"], 30000)).stdout
        ids = {line.split()[0] for line in models.splitlines() if line.strip()}
        if request.model_requested not in ids:
            raise ProviderError("MODEL_UNAVAILABLE", f"当前账号未列出请求模型：{request.model_requested}。")
        ref = {"cli_version": version, "config_isolation_verified": True, "runtime": str(runtime)}
        emit({"kind": "reference", "provider_ref": ref})
        final_results = []
        observed = None

        def event(item):
            nonlocal observed
            kind = item.get("event")
            if kind == "init":
                init = item.get("init", {})
                observed = init.get("model")
                ref["conversation_id"] = item.get("conversation_id")
                if init.get("permission_mode") == "always-proceed":
                    raise ProviderError("UNSAFE_PERMISSION_MODE", "文本节点不能使用自动批准所有工具的模式。", dispatched=True, provider_ref=ref)
                emit({"kind": "reference", "provider_ref": dict(ref)})
            elif kind == "step_update":
                update = item.get("step_update", {})
                if update.get("step_type") == "tool" or update.get("tool_name"):
                    raise ProviderError("UNEXPECTED_TOOL_CALL", "文本节点出现工具步骤，已停止自动工作流。", dispatched=True, provider_ref=ref)
                if update.get("text_delta"):
                    emit({"kind": "text", "text": update["text_delta"]})
            elif kind == "result":
                final_results.append(item.get("result"))

        argv = [executable, "--mode", "plan", "--sandbox", "--agent", AGENT,
                "--print-timeout", "150s", "--model", request.model_requested,
                "--input-format", "stream-json", "--output-format", "stream-json"]
        payload = json.dumps({"event": "user", "message": {"content": request.prompt}}, ensure_ascii=False).encode("utf-8") + b"\n"
        await run_process(argv, cwd=workspace, evidence=request.evidence_dir, label="generation",
                          timeout_ms=request.total_timeout_ms, cancel=request.cancel, emit=emit,
                          input_bytes=payload, env_overrides=env, parse_jsonl=True, on_event=event, model_request=True)
        if len(final_results) != 1 or not isinstance(final_results[0], dict):
            raise ProviderError("MISSING_RESULT", "单次请求没有得到唯一完整的结果事件。", dispatched=True, provider_ref=ref)
        result = final_results[0]
        if result.get("status") != "SUCCESS":
            raise provider_failure(str(result.get("error") or result.get("status")), dispatched=True, provider_ref=ref)
        body = result.get("structured_output")
        if body is None:
            body = result.get("response")
        if body is None or isinstance(body, str) and not body.strip():
            raise ProviderError("EMPTY_RESPONSE", "CLI 报告成功但正文为空。", dispatched=True, provider_ref=ref)
        return TextResult(json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else body, ref, observed)
