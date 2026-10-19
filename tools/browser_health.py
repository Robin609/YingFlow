"""Read-only health of an existing App-owned browser; never starts login."""
import argparse
import asyncio
import json
from pathlib import Path

from video_app.config import load_settings
from video_app.providers.gemini_web import BrowserSession


async def main(args):
    browser = BrowserSession(load_settings(args.settings), args.data_root.resolve())
    try:
        result = await browser.health()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await browser.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--output", type=Path)
    asyncio.run(main(parser.parse_args()))
