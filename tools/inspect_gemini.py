"""Read-only inspection of a specifically recorded App-owned Gemini page."""
import argparse
import asyncio
import json
from pathlib import Path

from video_app.config import load_settings
from video_app.providers.gemini_web import BrowserSession


async def main(args):
    session = BrowserSession(load_settings(), args.data_root.resolve())
    try:
        page = await session.page_for(args.task_id, args.url)
        args.output.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(args.output / "gemini-page.png"), full_page=True)
        snapshot = await page.locator("body").aria_snapshot()
        (args.output / "gemini-page.yaml").write_text(snapshot, encoding="utf-8")
        responses = page.locator("model-response")
        details = {"url": page.url, "responses": await responses.count(),
                   "response_text": await responses.last.inner_text() if await responses.count() else None,
                   "buttons": await page.get_by_role("button").all_text_contents(),
                   "message_content_count": await responses.locator("message-content").count(),
                   "code_count": await responses.locator("pre code").count()}
        copy = page.get_by_role("button", name="复制", exact=True)
        details["copy_inside_response"] = await responses.get_by_role("button", name="复制", exact=True).count()
        details["user_query_count"] = await page.locator("user-query").count()
        if await copy.count() == 1:
            details["copy_parent_chain"] = await copy.evaluate("e => { const a=[]; for(let n=e;n && a.length<6;n=n.parentElement) a.push(n.tagName); return a; }")
        if await responses.count():
            details["response_parent_chain"] = await responses.last.evaluate("e => { const a=[]; for(let n=e;n && a.length<6;n=n.parentElement) a.push(n.tagName); return a; }")
        (args.output / "gemini-inspection.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(details, ensure_ascii=False, indent=2))
    finally:
        await session.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/playwright"))
    asyncio.run(main(parser.parse_args()))
