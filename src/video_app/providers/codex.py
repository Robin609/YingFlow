"""Codex CLI text transport using normal official login, never credential copies."""
from pathlib import Path

from ..core.contracts import ProviderError, TextRequest, TextResult, provider_failure
from .process import run_process
from .base import TextTransport


class CodexProvider(TextTransport):
    def __init__(self, settings, data_root):
        super().__init__()
        self.settings = settings
        self.root = data_root

    async def _generate(self, request: TextRequest, emit) -> TextResult:
        executable = self.settings.get("codex_executable")
        if not executable or not Path(executable).is_file():
            raise ProviderError("CLI_NOT_FOUND", "请在连接页指定 Codex CLI 路径。")
        workspace = self.root / "runtimes/codex" / request.run_id / "workspace"
        version = await run_process([executable, "--version"], cwd=workspace, evidence=request.evidence_dir,
                                    label="version", timeout_ms=15000, cancel=request.cancel, emit=emit)
        ref = {"cli_version": version.stdout.strip()}
        messages = []
        complete = False

        def event(item):
            nonlocal complete
            kind = item.get("type")
            if kind == "thread.started":
                ref["thread_id"] = item.get("thread_id")
                emit({"kind": "reference", "provider_ref": dict(ref)})
            elif kind in {"turn.failed", "error"}:
                error = item.get("error", item.get("message", "CLI 轮次失败"))
                raise provider_failure(str(error), dispatched=True, provider_ref=ref)
            elif kind in {"item.started", "item.completed"}:
                part = item.get("item", {})
                if part.get("type") not in {"agent_message", "reasoning"}:
                    raise ProviderError("UNEXPECTED_TOOL_CALL", f"文本节点出现非预期执行项：{part.get('type')}。", dispatched=True, provider_ref=ref)
                if kind == "item.completed" and part.get("type") == "agent_message":
                    messages.append(part.get("text", ""))
                    emit({"kind": "text", "text": part.get("text", "")})
            elif kind == "turn.completed":
                complete = True

        argv = [executable, "--ask-for-approval", "never", "exec", "--ignore-user-config",
                "--skip-git-repo-check", "--ephemeral", "--json", "--model", request.model_requested,
                "--sandbox", "read-only", "--disable", "shell_tool", "--disable", "multi_agent",
                "--disable", "apps", "--disable", "plugins", "--disable", "browser_use",
                "--disable", "computer_use", "--disable", "image_generation", "--disable", "hooks",
                "-c", 'web_search="disabled"', "-"]
        home = self.settings.get("codex_home") or str(Path.home() / ".codex")
        await run_process(argv, cwd=workspace, evidence=request.evidence_dir, label="generation",
                          timeout_ms=request.total_timeout_ms, cancel=request.cancel, emit=emit,
                          input_bytes=request.prompt.encode("utf-8"), env_overrides={"CODEX_HOME": home},
                          parse_jsonl=True, on_event=event, model_request=True)
        if not complete:
            raise ProviderError("MISSING_RESULT", "缺少 turn.completed，不能发布部分正文。", dispatched=True, provider_ref=ref)
        body = messages[-1] if messages else ""
        if not body.strip():
            raise ProviderError("EMPTY_RESPONSE", "轮次结束但没有完整正文。", dispatched=True, provider_ref=ref)
        # A requested model is configuration, not an observation of actual routing.
        return TextResult(body, ref, None)
