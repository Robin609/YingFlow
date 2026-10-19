"""Dedicated Chrome/CDP transport with one dispatch and visible-answer reads."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.parse import urlparse

from ..core.contracts import ProviderError, TextRequest, TextResult
from .base import TextTransport


MODE_PICKER = re.compile(r"模式选择器|mode picker", re.I)


def process_identity(pid: int, port: int) -> dict:
    # Inspect only the recorded owned PID and configured local port. Never
    # enumerate browser profiles, cookies or other Chrome tabs.
    if type(pid) is not int or pid <= 0 or not 1024 <= port <= 65535:
        return {}
    script = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
              f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}';"
              f"$l=Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue;"
              "@{process=$p|Select-Object ProcessId,CommandLine,CreationDate;listeners=@($l|Select-Object -ExpandProperty OwningProcess -Unique)}|ConvertTo-Json -Compress")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        return json.loads(result.stdout.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError:
        return {}


class BrowserSession:
    def __init__(self, settings, root):
        self.settings = settings
        self.root = Path(root)
        self.profile = Path(settings.get("gemini_profile_dir") or self.root / "browser/gemini").resolve()
        daily = Path.home() / "AppData/Local/Google/Chrome/User Data"
        if self.profile == daily.resolve() or self.profile.is_relative_to(daily.resolve()):
            raise ProviderError("BROWSER_PROFILE", "请使用 App 专用浏览器目录，不使用日常 Chrome 用户目录。")
        self.port = int(settings.get("browser_port") or 9222)
        if not 1024 <= self.port <= 65535:
            raise ProviderError("BROWSER_PORT", "浏览器端口必须在 1024 至 65535 之间。")
        self.endpoint = f"http://127.0.0.1:{self.port}"
        # The App opts in by default; direct callers with a fixed port retain
        # the original strict contract unless they explicitly enable this.
        self.auto_port = bool(settings.get("browser_auto_port", False))
        self.playwright = None
        self.browser = None
        self.pages = {}

    def _set_port(self, port):
        self.port = port
        self.endpoint = f"http://127.0.0.1:{port}"

    def _saved_session(self):
        # One record per profile; read the old shared record for compatibility.
        for path in (self.profile.parent / f".{self.profile.name}.session.json", self.profile.parent / "session.json"):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            except OSError as error:
                raise ProviderError("BROWSER_PROFILE_UNAVAILABLE", "无法读取专用浏览器配置。请在连接助手中选择可访问的目录或创建新专用窗口。") from error
            if isinstance(saved, dict) and saved.get("profile") == str(self.profile):
                return saved
        return {}

    async def _reuse_recorded(self, saved):
        preferred = self.port
        recorded = saved.get("port")
        if self.auto_port and type(recorded) is int and 1024 <= recorded <= 65535:
            self._set_port(recorded)
        if self._port_open() and await asyncio.to_thread(self._owns, saved):
            return True
        self._set_port(preferred)
        return False

    def _port_open(self):
        with socket.socket() as connection:
            connection.settimeout(0.2)
            return connection.connect_ex(("127.0.0.1", self.port)) == 0

    def _owns(self, saved):
        if saved.get("profile") != str(self.profile.resolve()) or saved.get("port") != self.port:
            return False
        identity = process_identity(saved.get("pid"), self.port)
        process = identity.get("process") or {}
        command = str(process.get("CommandLine", "")).replace('"', '').lower() + " "
        expected = f"--user-data-dir={str(self.profile.resolve()).lower()} "
        return (expected in command and saved.get("pid") in identity.get("listeners", [])
                and (not saved.get("creation_date") or saved["creation_date"] == process.get("CreationDate")))

    async def open(self):
        if self.browser and self.browser.is_connected():
            return {"endpoint": self.endpoint, "profile": str(self.profile), "port": self.port, "status": "connected"}
        executable = self.settings.get("chrome_executable")
        if not executable or not Path(executable).is_file():
            raise ProviderError("CLI_NOT_FOUND", "没有找到 Chrome。请在连接助手中重新查找或选择本机 chrome.exe。")
        try:
            self.profile.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ProviderError("BROWSER_PROFILE_UNAVAILABLE", "无法创建专用浏览器目录。请在连接助手中使用本机新专用窗口。") from error
        metadata = self.profile.parent / f".{self.profile.name}.session.json"
        saved = self._saved_session()
        owned = await self._reuse_recorded(saved)
        if not owned and self._port_open():
            if not self.auto_port:
                raise ProviderError("BROWSER_PORT_IN_USE", f"固定端口 {self.port} 已被其他实例占用。请用连接助手开启自动选择端口；本轮请求尚未发送。")
            # Ask Windows for an available local port. Still verify ownership
            # after launch: another process winning the race is never attached.
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                self._set_port(available.getsockname()[1])
        if not owned:
            child = subprocess.Popen([executable, f"--user-data-dir={self.profile.resolve()}",
                                      "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={self.port}",
                                      "--no-first-run", "--no-default-browser-check", "https://gemini.google.com/app"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if self._port_open():
                    break
                if child.poll() is not None:
                    raise ProviderError("BROWSER_START_FAILED", "专用目录可能已被未登记的窗口使用。请先关闭该专用窗口再试，或在连接助手中使用新专用窗口；无需关闭日常 Chrome。")
                await asyncio.sleep(0.1)
            saved = {"pid": child.pid, "port": self.port, "profile": str(self.profile.resolve())}
            identity = await asyncio.to_thread(process_identity, child.pid, self.port)
            saved["creation_date"] = (identity.get("process") or {}).get("CreationDate")
            if not await asyncio.to_thread(self._owns, saved):
                raise ProviderError("BROWSER_OWNERSHIP_UNVERIFIED", "无法核对专用实例与调试端口的归属，未连接其他浏览器。")
            try:
                metadata.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as error:
                raise ProviderError("BROWSER_PROFILE_UNAVAILABLE", "专用窗口已打开，但无法保存连接记录。请保留该窗口并检查目录写入权限。") from error
        from playwright.async_api import async_playwright
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(self.endpoint, timeout=10000)
        except Exception as error:
            await self.disconnect()
            raise ProviderError("BROWSER_CONNECT_FAILED", "专用窗口尚未连接成功。请回到连接助手重新检查，未发送文稿。") from error
        return {"endpoint": self.endpoint, "profile": str(self.profile), "port": self.port, "status": "connected"}

    async def page_for(self, task_id, url=None):
        await self.open()
        if task_id in self.pages and not self.pages[task_id].is_closed():
            return self.pages[task_id]
        context = self.browser.contexts[0]
        if url:
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != "gemini.google.com":
                raise ProviderError("BROWSER_REFERENCE", "原会话地址不是 Gemini。")
            matches = [p for p in context.pages if p.url == url]
            if len(matches) != 1:
                raise ProviderError("UNKNOWN_RESULT", "原会话页面不能唯一确认，请保留现场并人工核对。", dispatched=True)
            page = matches[0]
        else:
            page = await context.new_page()
            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=30000)
        self.pages[task_id] = page
        return page

    async def ready_composer(self, page):
        for _ in range(50):
            if urlparse(page.url).hostname != "gemini.google.com":
                raise ProviderError("AUTH_REQUIRED", "请先在 App 专用浏览器中完成 Gemini 登录。")
            for role in ("button", "link"):
                login = page.get_by_role(role, name=re.compile(r"^(登录|登入|Sign in)$", re.I))
                if any([await item.is_visible() for item in await login.all()]):
                    raise ProviderError("AUTH_REQUIRED", "请在 App 专用 Chrome 中登录 Gemini；未向游客模式提交请求。")
            boxes = page.locator('[contenteditable="true"][role="textbox"]')
            if await boxes.count() == 1 and await boxes.is_visible():
                return boxes
            await asyncio.sleep(0.2)
        raise ProviderError("UI_CHANGED", "未找到唯一可编辑的 Gemini 输入框；请处理登录或页面验证。")

    async def ensure_mode(self, page, expected=None, *, select=True):
        """Match the visible menu label, never invent an API model identifier."""
        from playwright.async_api import Error as PlaywrightError
        expected = expected or self.settings.get("gemini_mode") or "4 Argon"
        box = await self.ready_composer(page)
        picker = page.get_by_role("button", name=MODE_PICKER)
        menu = page.get_by_role("menu")

        async def open_menu():
            # Gemini's home composer hides its toolbar until focused.
            await box.click(timeout=5000)
            await picker.wait_for(state="visible", timeout=5000)
            await picker.click(timeout=5000)
            await menu.wait_for(state="visible", timeout=5000)
            if await menu.count() != 1:
                raise ProviderError("UI_CHANGED", "无法唯一识别 Gemini 模式菜单，未发送文稿。")

        def target_item():
            return menu.get_by_role("menuitem").filter(has=page.get_by_text(expected, exact=True))

        async def selected(item):
            # Observed menu exposes a selected checkmark, not a radio role.
            marker = item.locator('[aria-label="已选中"], [aria-label="Selected"]')
            return await marker.count() == 1 or await item.get_attribute("aria-checked") == "true"

        try:
            await open_menu()
            item = target_item()
            if await item.count() != 1 or not await item.is_enabled():
                choices = []
                for option in await menu.get_by_role("menuitem").all():
                    if await option.get_attribute("data-mode-id"):
                        choices.append((await option.inner_text()).strip().splitlines()[0])
                raise ProviderError("MODEL_UNAVAILABLE", f"当前网页没有可用的「{expected}」。可见模式：{'、'.join(choices) or '未识别'}。未改用默认 Flash，也未发送文稿。")
            if not await selected(item):
                if not select:
                    raise ProviderError("MODEL_MISMATCH", f"发送前模式已不是「{expected}」，本轮已暂停，未发送文稿。")
                await item.click(timeout=5000)
                await page.keyboard.press("Escape")
                await open_menu()
                item = target_item()
                if await item.count() != 1 or not await selected(item):
                    raise ProviderError("MODEL_MISMATCH", f"未能确认已选中「{expected}」，未发送文稿。")
            return {"mode_requested": expected, "mode_selected": expected, "mode_verified": True,
                    "model_display_observed": await picker.get_attribute("aria-label")}
        except PlaywrightError as error:
            raise ProviderError("UI_CHANGED", "Gemini 模式选择器未就绪，未发送文稿。请在连接助手中重新检查。") from error
        finally:
            if not page.is_closed() and await menu.count() == 1 and await menu.is_visible():
                await page.keyboard.press("Escape")

    async def health(self, *, launch=False, ensure_mode=False):
        if not launch and not await self._reuse_recorded(self._saved_session()):
            return {"status": "not_running", "auth_observed": "unknown", "profile": str(self.profile), "port": self.port}
        result = await self.open()
        observed = []
        for page in self.browser.contexts[0].pages:
            # Do not read account-login forms, credential fields or auth URLs.
            parsed = urlparse(page.url)
            if parsed.hostname != "gemini.google.com" or not parsed.path.startswith("/app"):
                continue
            required = False
            for role in ("button", "link"):
                login = page.get_by_role(role, name=re.compile(r"^(登录|登入|Sign in)$", re.I))
                required = required or any([await item.is_visible() for item in await login.all()])
            boxes = page.locator('[contenteditable="true"][role="textbox"]')
            mode = page.get_by_role("button", name=MODE_PICKER)
            observed.append({"login_visible": required, "composer_count": await boxes.count(),
                             "composer_visible": await boxes.count() == 1 and await boxes.is_visible(),
                             "model_display": await mode.get_attribute("aria-label") if await mode.count() == 1 else None})
        ready = any(not item["login_visible"] and item["composer_visible"] for item in observed)
        result.update(auth_observed="sign_in_not_visible" if ready else "login_required" if observed else "user_action_in_progress",
                      gemini_pages=observed, generation_verified=False, mode_verified=False)
        if ready and ensure_mode:
            # Configure only our own setup page, never an existing conversation.
            setup_page = await self.page_for("connection-setup")
            result.update(await self.ensure_mode(setup_page))
            result["auth_observed"] = "sign_in_not_visible"
        return result

    async def disconnect(self):
        if self.playwright:
            await self.playwright.stop()
        self.browser = None
        self.playwright = None
        # The interactive browser and its login profile deliberately stay open.


class GeminiProvider(TextTransport):
    def __init__(self, session):
        super().__init__()
        self.session = session

    async def _generate(self, request: TextRequest, emit) -> TextResult:
        page = await self.session.page_for(request.task_id)
        if request.cancel.is_set():
            raise ProviderError("CANCELLED", "已取消，未发送文稿。")
        mode = await self.session.ensure_mode(page, request.model_requested)
        boxes = page.locator('[contenteditable="true"][role="textbox"]')
        answers = page.locator("model-response")
        before = await answers.count()
        ref = {"url": page.url, "task_id": request.task_id, "run_id": request.run_id,
               "answers_before": before, "adapter_version": "gemini-dom-v2", **mode}
        if request.cancel.is_set():
            raise ProviderError("CANCELLED", "已取消，未发送文稿。")
        await boxes.fill(request.prompt)
        # A user/website mode change after preparation must not silently select
        # another model when the send button is about to be clicked.
        ref.update(await self.session.ensure_mode(page, request.model_requested, select=False))
        send = page.get_by_role("button", name=re.compile(r"^(发送|Send message|Send)$", re.I))
        if await send.count() != 1 or not await send.is_enabled():
            raise ProviderError("UI_CHANGED", "发送按钮不能唯一确认，未重复点击。")
        if request.cancel.is_set():
            raise ProviderError("CANCELLED", "已取消，未发送文稿。")
        ref["dispatch_intent_at"] = datetime.now(timezone.utc).isoformat()
        emit({"kind": "reference", "provider_ref": dict(ref)})
        emit({"kind": "dispatched", "dispatched": True})
        try:
            await send.click(timeout=10000)
        except Exception as error:
            raise ProviderError("DISPATCH_UNCONFIRMED", "发送操作结果不确定，需核对原页面，不能直接重发。", dispatched=True, provider_ref=ref) from error
        ref["url"] = page.url
        emit({"kind": "reference", "provider_ref": dict(ref)})
        return await self.read_result(request, page, ref, emit)

    async def read_result(self, request, page, ref, emit):
        start = time.monotonic()
        previous = None
        stable = 0
        while (time.monotonic() - start) * 1000 < request.total_timeout_ms:
            if request.cancel.is_set():
                raise ProviderError("CANCELLED", "已停止本地等待；网页生成是否取消需核对。", dispatched=True, provider_ref=ref)
            ref["url"] = page.url
            emit({"kind": "reference", "provider_ref": dict(ref)})
            answers = page.locator("model-response")
            if await answers.count() > ref["answers_before"]:
                answer = answers.last
                code = answer.locator("pre code")
                content = answer.locator("message-content")
                text = await code.inner_text() if await code.count() == 1 else await content.inner_text() if await content.count() == 1 else ""
                stop = page.get_by_role("button", name=re.compile(r"停止|Stop response|Stop generating", re.I))
                copy = answer.get_by_role("button", name=re.compile(r"复制|Copy", re.I))
                stopped = not any([await locator.is_visible() for locator in await stop.all()])
                complete_control = any([await locator.is_visible() for locator in await copy.all()])
                if text and text == previous:
                    stable += 1
                else:
                    stable = 0
                    if text:
                        emit({"kind": "preview", "text": text[-2000:]})
                previous = text
                if stopped and complete_control and stable >= 1:
                    # Completion and content validity are separate. Return a
                    # completed malformed answer to core validation/one repair,
                    # rather than timing out a response that has already ended.
                    request.evidence_dir.mkdir(parents=True, exist_ok=True)
                    (request.evidence_dir / "web-visible-answer.txt").write_text(await answer.inner_text(), encoding="utf-8")
                    (request.evidence_dir / "web-extracted-json.txt").write_text(text, encoding="utf-8")
                    emit({"kind": "reference", "provider_ref": dict(ref)})
                    return TextResult(text, ref, ref.get("mode_selected"))
            await asyncio.sleep(0.7)
        raise ProviderError("PROCESS_TIMEOUT", "Gemini 回答未确认完成，原页面与会话已保留。", dispatched=True, provider_ref=ref)
