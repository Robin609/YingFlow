"""Build a new, separately versioned Windows folder/ZIP; never touch user data."""
import argparse
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tomllib


def main():
    parser = argparse.ArgumentParser(description="制作 Windows x64 免安装包")
    parser.add_argument("--output-dir", type=Path, help="全新的输出目录；已有目录不会被覆盖")
    args = parser.parse_args()
    if os.name != "nt" or struct.calcsize("P") != 8:
        parser.error("请使用 Windows x64 的 Python 环境打包。")
    try:
        builder = importlib.metadata.version("PyInstaller")
    except importlib.metadata.PackageNotFoundError:
        parser.error("请先在本项目虚拟环境安装 requirements-build.txt。")
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root/"pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    tag = "VideoWorkbench-" + version + "-win64-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    output = (args.output_dir or root/"dist"/tag).resolve()
    if output.exists() or Path(str(output) + ".zip").exists():
        parser.error("输出位置已存在，请选择一个新目录；原包保留。")
    output.mkdir(parents=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--distpath", str(output),
                    "--workpath", str(root/"build"/tag), str(root/"packaging/VideoWorkbench.spec")],
                   cwd=root, check=True)
    bundle = output/"VideoWorkbench"
    shutil.copyfile(root/"docs/portable-app.md", bundle/"使用说明.md")
    for line in (root/"requirements.lock.txt").read_text(encoding="utf-8-sig").splitlines():
        if "==" not in line:
            continue
        name = line.split("==", 1)[0]
        distribution = importlib.metadata.distribution(name)
        for item in distribution.files or []:
            parts = Path(item).parts
            lowered = [part.lower() for part in parts]
            if "licenses" in lowered:
                relative = Path(*parts[lowered.index("licenses")+1:])
            elif Path(item).name.lower().startswith(("license", "copying", "notice")):
                relative = Path(item).name
            else:
                continue
            source = distribution.locate_file(item)
            target = bundle/"licenses"/name/relative
            if source.is_file() and target.resolve().is_relative_to((bundle/"licenses").resolve()):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    info = {"app_version": version, "python": platform.python_version(), "builder": builder,
            "platform": "windows-x64", "user_data_included": False,
            "browser_login_included": False,
            "requirements": {name: importlib.metadata.version(name) for name in
                             ("PySide6", "playwright", "pydantic", "imageio-ffmpeg", "edge-tts", "google-auth")}}
    (bundle/"build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = shutil.make_archive(str(output), "zip", root_dir=output, base_dir="VideoWorkbench")
    print(json.dumps({"bundle": str(bundle), "archive": archive, "built_only": True,
                      "runtime_verified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
