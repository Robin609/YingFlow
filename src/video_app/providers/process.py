"""Bounded child-process transport, incremental JSONL and owned cancellation."""
from __future__ import annotations

import asyncio
import codecs
from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time

from ..core.contracts import ProviderError


def redact(text: str) -> str:
    text = re.sub(r'(?i)("?(?:access_token|refresh_token|id_token|api_key|authorization)"?\s*[:=]\s*")[^"]*(")', r'\1[REDACTED]\2', text)
    return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)


@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: int


async def terminate_owned(process):
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            await asyncio.wait_for(killer.wait(), 5)
        except TimeoutError:
            killer.kill()
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await asyncio.wait_for(process.wait(), 5)


async def run_process(argv: list[str], *, cwd: Path, evidence: Path, label: str,
                      timeout_ms: int, cancel, emit, input_bytes=None, env_overrides=None,
                      parse_jsonl=False, on_event=None, model_request=False) -> ProcessResult:
    evidence.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    dispatched = False
    process = None
    chunks = {"stdout": [], "stderr": []}
    metadata = {"executable": argv[0], "argument_names": [a for a in argv[1:] if a.startswith("-")],
                "timeout_ms": timeout_ms, "dispatched": False,
                "input_sha256": hashlib.sha256(input_bytes).hexdigest() if input_bytes is not None else None}

    def save_meta():
        (evidence / f"{label}.meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        if cancel.is_set():
            raise ProviderError("CANCELLED", "任务已取消。")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd), env={**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8", **(env_overrides or {})},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as error:
            raise ProviderError("PROCESS_START_FAILED", str(error)) from error
        metadata["pid"] = process.pid
        save_meta()
        emit({"kind": "process", "pid": process.pid})

        async def read_stream(name, stream):
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            pending = ""
            with (evidence / f"{label}.{name}.txt").open("w", encoding="utf-8", newline="") as log:
                while True:
                    chunk = await stream.read(8192)
                    if not chunk:
                        tail = decoder.decode(b"", final=True)
                        chunks[name].append(tail)
                        pending += tail
                        if pending:
                            log.write(redact(pending))
                            log.flush()
                            if parse_jsonl and name == "stdout" and pending.strip():
                                handle_line(pending)
                        break
                    text = decoder.decode(chunk)
                    chunks[name].append(text)
                    pending += text
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        log.write(redact(line) + "\n")
                        log.flush()
                        if parse_jsonl and name == "stdout" and line.strip():
                            handle_line(line)

        def handle_line(line):
            try:
                event = json.loads(line.lstrip("\ufeff"))
            except json.JSONDecodeError as error:
                raise ProviderError("INVALID_PROTOCOL_JSON", "CLI 返回了无法解析的事件行。", dispatched=dispatched) from error
            if not isinstance(event, dict):
                raise ProviderError("INVALID_PROTOCOL_JSON", "CLI 事件不是对象。", dispatched=dispatched)
            if on_event:
                on_event(event)

        async def write_input():
            nonlocal dispatched
            if model_request:
                # Persist intention before writing. A crash or broken pipe from
                # this point is reconciled rather than automatically resubmitted.
                dispatched = True
                metadata["dispatched"] = True
                save_meta()
                emit({"kind": "dispatched", "dispatched": True})
            try:
                if input_bytes is not None:
                    process.stdin.write(input_bytes)
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        tasks = [asyncio.create_task(read_stream("stdout", process.stdout)),
                 asyncio.create_task(read_stream("stderr", process.stderr)),
                 asyncio.create_task(write_input()), asyncio.create_task(process.wait())]
        try:
            deadline = start + timeout_ms / 1000
            while not all(task.done() for task in tasks):
                for task in tasks:
                    if task.done() and task.exception() is not None:
                        raise task.exception()
                if cancel.is_set():
                    raise ProviderError("CANCELLED", "已停止本地等待并结束 App 拥有的子进程；外部状态需核对。", dispatched=dispatched)
                if time.monotonic() >= deadline:
                    raise ProviderError("PROCESS_TIMEOUT", "宿主总时间预算已到，原始输出已保留。", dispatched=dispatched)
                await asyncio.sleep(0.05)
            await asyncio.gather(*tasks)
        finally:
            if process.returncode is None:
                await terminate_owned(process)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        stdout, stderr = "".join(chunks["stdout"]), "".join(chunks["stderr"])
        if re.search(r"print timeout after", stderr, re.I):
            raise ProviderError("CLI_PRINT_TIMEOUT", "CLI 内部生成超时。", dispatched=dispatched)
        if process.returncode != 0:
            combined = (stderr + "\n" + stdout).lower()
            code = "AUTH_REQUIRED" if any(t in combined for t in ["not logged in", "authentication required", "unauthorized", "not authenticated"]) else "CLI_EXIT_ERROR"
            raise ProviderError(code, redact((stderr or stdout).strip())[-1800:] or f"CLI 退出码 {process.returncode}", dispatched=dispatched)
        return ProcessResult(stdout, stderr, process.returncode, round((time.monotonic() - start) * 1000))
    finally:
        metadata.update(exit_code=process.returncode if process else None, elapsed_ms=round((time.monotonic() - start) * 1000))
        for name, values in chunks.items():
            original = "".join(values)
            safe = redact(original)
            metadata[f"{name}_redacted"] = safe != original
            (evidence / f"{label}.{name}.txt").write_text(safe, encoding="utf-8", newline="")
        save_meta()
