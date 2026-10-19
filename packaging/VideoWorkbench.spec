# Windows x64 one-folder app. Only whitelisted package assets enter the bundle.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
data = (collect_data_files("video_app", includes=["render/*.ps1", "assets/speech/*.mp3"])
        + collect_data_files("imageio_ffmpeg", includes=["binaries/**"])
        + collect_data_files("playwright", includes=["driver/**"]))
a = Analysis([str(root / "packaging/desktop_entry.py")],
             pathex=[str(root / "src")], binaries=[], datas=data,
             hiddenimports=[], hookspath=[], runtime_hooks=[], excludes=[])
# Qt 6.12 imports the Windows ICU API. The build Python distribution also
# carries an unrelated ICU DLL with suffixed exports; freezing that DLL at
# the bundle root shadows Windows' ICU and prevents QtCore from loading.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() != "icuuc.dll"]
pyz = PYZ(a.pure)
options = [("X utf8", None, "OPTION")]
desktop = EXE(pyz, a.scripts, options, exclude_binaries=True,
              name="VideoWorkbench", console=False, debug=False, strip=False, upx=False)
worker = EXE(pyz, a.scripts, options, exclude_binaries=True,
             name="VideoWorkbenchWorker", console=True, debug=False, strip=False, upx=False)
bundle = COLLECT(desktop, worker, a.binaries, a.datas,
                 name="VideoWorkbench", strip=False, upx=False)
