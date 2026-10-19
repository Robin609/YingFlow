"""Owned rendering subprocess: Windows speech, vector frames and bundled FFmpeg."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import wave


def emit(message, percent):
    print(json.dumps({"phase": "progress", "message": message, "percent": percent}, ensure_ascii=False), flush=True)


def clauses(text):
    text = re.sub(r"\s+", " ", text).strip()
    result = []
    for phrase in re.split(r"(?<=[，。！？；])", text):
        for index in range(0, len(phrase), 28):
            part = phrase[index:index+28].strip()
            if part:
                result.append(part)
    return result


def clock(ms):
    ms = round(ms)
    return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"


def render(path):
    import imageio_ffmpeg
    from PySide6.QtGui import QGuiApplication
    from .frames import FrameArtist
    from .imports import ImportedVisual
    from .speech import synthesize
    from ..ui.theme import load_system_fonts

    folder = path.parent.resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if Path(data["output_dir"]).resolve() != folder:
        raise ValueError("视频任务输出目录不匹配。")
    if os.name != "nt":
        raise ValueError("当前本机配音路线需要 Windows。")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication([])
    load_system_fonts()
    fps, sample_rate = 30, 24000
    height = int(data["quality"])
    ratios = {"4:3": (4, 3), "16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}
    a, b = ratios[data["aspect"]]
    # Portrait quality denotes its short edge; all dimensions stay even.
    width, height = (height, round(height*b/a/2)*2) if a < b else (round(height*a/b/2)*2, height)
    audio_dir = folder / "audio"
    audio_dir.mkdir(exist_ok=True)
    segments = []
    for index, shot in enumerate(data["shots"]):
        parts = clauses(shot["narration"])
        if not parts:
            raise ValueError("有镜头缺少可朗读的旁白。")
        for part in parts:
            segments.append({"scene": index, "text": part, "path": str(audio_dir / f"{len(segments):04d}.wav")})
    speech_manifest = folder / "speech.json"
    speech = synthesize(data.get("speech") or {"provider": "windows", "voice": data["voice"], "rate": data["rate"]},
                        segments, speech_manifest, emit, google_confirmed=data.get("google_confirmed", False))
    voice = speech["voice"]
    captions, timeline = [], []
    sample_cursor = 0
    audio_file = folder / "narration.wav"
    with wave.open(str(audio_file), "wb") as output:
        output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        for index, shot in enumerate(data["shots"]):
            start_sample = sample_cursor
            for segment in (s for s in segments if s["scene"] == index):
                with wave.open(segment["path"], "rb") as source:
                    if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, sample_rate):
                        raise ValueError("配音格式不一致，停止合成。")
                    count = source.getnframes()
                    if count < sample_rate*.08:
                        raise ValueError("配音片段为空或过短，停止合成。")
                    output.writeframesraw(source.readframes(count))
                captions.append({"start_ms": sample_cursor*1000/sample_rate, "end_ms": (sample_cursor+count)*1000/sample_rate, "text": segment["text"]})
                gap = round(sample_rate*.06)
                output.writeframesraw(b"\0\0"*gap)
                sample_cursor += count+gap
            pad = round(sample_rate*.28)
            sample_cursor += pad
            rounded = math.ceil(sample_cursor/(sample_rate/fps))*int(sample_rate/fps)
            output.writeframesraw(b"\0\0"*(pad+rounded-sample_cursor))
            sample_cursor = rounded
            timeline.append({**shot, "start_frame": round(start_sample*fps/sample_rate), "end_frame": round(sample_cursor*fps/sample_rate)})
    total_frames = round(sample_cursor*fps/sample_rate)
    duration_ms = total_frames*1000/fps
    (folder / "captions.srt").write_text("\n".join(f"{i+1}\n{clock(c['start_ms'])} --> {clock(c['end_ms'])}\n{c['text']}\n" for i,c in enumerate(captions)), encoding="utf-8")
    (folder / "timeline.json").write_text(json.dumps({"shots": timeline, "captions": captions}, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(f"配音已完成，开始渲染 {len(timeline)} 个镜头…", 22)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    temporary = folder / "video.part.mp4"
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-i", str(audio_file),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "4", "-c:a", "aac", "-ar", "48000", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(temporary)]
    artist = FrameArtist(width, height, data["title"], len(timeline))
    process = None
    scene_index = caption_index = 0
    visual = None
    visual_scene = None
    visual_reports = []

    def close_visual():
        nonlocal visual
        if visual:
            visual_reports.append({"shot_id": timeline[visual_scene]["shot_id"], **visual.report()})
            visual.close()
            visual = None
    with (folder / "ffmpeg.log").open("wb") as error_log:
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=error_log, creationflags=subprocess.CREATE_NO_WINDOW)
            for frame in range(total_frames):
                ms = frame*1000/fps
                while scene_index < len(timeline)-1 and frame >= timeline[scene_index]["end_frame"]:
                    scene_index += 1
                while caption_index < len(captions)-1 and ms >= captions[caption_index]["end_ms"]:
                    caption_index += 1
                cue = captions[caption_index]
                caption = cue["text"] if cue["start_ms"] <= ms < cue["end_ms"] else ""
                scene = timeline[scene_index]
                if visual_scene != scene_index:
                    close_visual()
                    visual_scene = scene_index
                    if scene.get("imported_visual"):
                        emit(f"正在检查镜头 {scene_index+1} 的导入画面…", 22+int(frame/total_frames*70))
                        visual = ImportedVisual(scene["imported_visual"], round(width*.84), round(height*.37), scene["end_frame"]-scene["start_frame"], fps)
                image = artist.frame(scene, caption, (frame-scene["start_frame"])/fps, frame/max(1,total_frames-1), scene_index,
                                     visual.next_frame() if visual else None)
                if frame == min(fps, total_frames-1):
                    image.save(str(folder / "cover.png"))
                process.stdin.write(image.constBits())
                if frame % fps == 0:
                    emit(f"正在渲染镜头 {scene_index+1}/{len(timeline)} · {ms/1000:.0f}/{duration_ms/1000:.0f} 秒", 22+int(frame/total_frames*70))
            process.stdin.close()
            if process.wait(timeout=90):
                raise RuntimeError("视频编码失败，请查看成片目录的 ffmpeg.log。")
        finally:
            try:
                close_visual()
            finally:
                if process and process.poll() is None:
                    process.kill()
                    process.wait()
    emit("正在检查完整视频与音轨…", 95)
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(temporary)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
    probe_text = probe.stderr.decode("utf-8", errors="replace")
    (folder / "media-info.txt").write_text(probe_text, encoding="utf-8")
    if "Video: h264" not in probe_text or "Audio: aac" not in probe_text or f"{width}x{height}" not in probe_text:
        raise RuntimeError("成片视频或音轨规格未通过检查。")
    decoded = subprocess.run([ffmpeg, "-v", "error", "-xerror", "-i", str(temporary), "-map", "0:v:0", "-map", "0:a:0", "-progress", "pipe:1", "-nostats", "-f", "null", "-"], capture_output=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
    stats = dict(re.findall(r"^(\w+)=(.*)$", decoded.stdout.decode("utf-8"), re.M))
    actual_frames = int(stats.get("frame", "0"))
    if decoded.returncode or abs(actual_frames-total_frames) > 1:
        raise RuntimeError("成片未能完整解码或帧数不匹配，未发布为完成。")
    video = folder / "video.mp4"
    temporary.replace(video)
    result = {"video_file": str(video), "subtitle_file": str(folder / "captions.srt"), "audio_file": str(audio_file), "cover_file": str(folder / "cover.png"),
              "duration_ms": round(duration_ms), "planned_ms": data["planned_ms"], "voice": voice, "speech": speech,
              "scope": "local_mixed_media" if visual_reports else "local_technical_animation", "illustration_not_measurement": not bool(visual_reports),
              "visual_assets": visual_reports,
              "quality": {"width": width, "height": height, "fps": fps, "video_codec": "h264", "audio_codec": "aac", "audio_rate": 48000, "decoded_frames": actual_frames, "full_decode_passed": True}}
    (folder / "render.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("MP4 与字幕已生成并检查完成。", 100)


def main(path=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        render(Path(path or sys.argv[1]).resolve())
    except Exception as error:
        print(f"视频制作失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
