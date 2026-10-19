"""Decode only the selected picture/video range; retain aspect and final frame."""
from __future__ import annotations

from pathlib import Path
import subprocess

import imageio_ffmpeg
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader


class ImportedVisual:
    def __init__(self, spec, max_width, max_height, frames, fps):
        self.spec, self.frames, self.fps = spec, frames, fps
        self.reader = None
        self.last = None
        self.decoded = 0
        self.ended = False
        path = Path(spec["path"])
        stat = path.stat()
        if stat.st_size != spec["size"] or stat.st_mtime_ns != spec["mtime_ns"]:
            raise ValueError("导入素材已被外部修改，请重新导入并应用。")
        image_reader = QImageReader(str(path))
        image_reader.setAutoTransform(True)
        if image_reader.canRead() and image_reader.imageCount() <= 1:
            size = image_reader.size()
            self.meta = {"kind": "image", "source_size": [size.width(), size.height()]}
            image_reader.setScaledSize(size.scaled(QSize(max_width, max_height), Qt.AspectRatioMode.KeepAspectRatio))
            self.last = image_reader.read()
            if self.last.isNull():
                raise ValueError("无法解码导入的图片。")
            self.decoded = 1
        else:
            self.meta = {"kind": "video"}
            # A short video is allowed to end normally. A decode error inside
            # the used range is not silently treated as a short video.
            verified = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-xerror", "-i", str(path),
                                       "-t", str(frames/fps), "-map", "0:v:0", "-an", "-f", "null", "-"],
                                      capture_output=True, timeout=max(30, min(300, round(frames/fps*2))),
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            if verified.returncode:
                raise ValueError("导入视频的使用区段无法正常解码，请换用可播放的画面素材。")
            w, h = max(4, max_width//4*4), max(4, max_height//4*4)
            self.reader = imageio_ffmpeg.read_frames(str(path), output_params=["-vf", f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=4", "-an", "-threads", "1"])
            try:
                info = next(self.reader)
                self.meta.update(source_size=list(info["source_size"]), source_duration_ms=round(info.get("duration", 0)*1000))
                self.size = info["size"]
            except Exception:
                self.close()
                raise ValueError("无法读取导入视频的画面规格。")

    def next_frame(self):
        if self.reader and not self.ended:
            try:
                raw = next(self.reader)
                w, h = self.size
                self.last = QImage(raw, w, h, w*3, QImage.Format.Format_RGB888).copy()
                self.decoded += 1
            except StopIteration:
                self.ended = True
        if self.last is None or self.last.isNull():
            raise ValueError("导入素材没有可用的画面。")
        return self.last

    def report(self):
        return {"asset_id": self.spec["asset_id"], "name": self.spec["name"], **self.meta,
                "used_scene_ms": round(self.frames*1000/self.fps), "used_frames_decoded": self.decoded,
                "held_frames": max(0, self.frames-self.decoded) if self.meta["kind"] == "video" else 0,
                "end_policy": "hold_last", "source_audio": "muted", "used_range_decode_passed": True}

    def close(self):
        if self.reader:
            self.reader.close()
            self.reader = None
