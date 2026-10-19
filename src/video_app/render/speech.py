"""Explicit speech routes; keep legacy Windows callers and never swap voices."""
from __future__ import annotations

import asyncio
import base64
import json
import math
from pathlib import Path
import subprocess


def speech_options(values):
    provider = values.get("speech_provider", "windows")
    if provider == "windows":
        rate = int(values.get("rate", 1))
        if not -3 <= rate <= 4:
            raise ValueError("本机语速应在 -3 到 4 之间。")
        return {"provider": provider, "voice": values.get("voice") or "", "rate": rate}
    if provider == "edge":
        rate, pitch = int(values.get("edge_rate", 10)), int(values.get("edge_pitch", -5))
        if not -50 <= rate <= 100 or not -50 <= pitch <= 50:
            raise ValueError("Edge 语速应在 -50% 到 +100%，音调在 -50 到 +50 Hz。")
        voice = str(values.get("edge_voice") or "zh-CN-YunjianNeural").strip()
        return {"provider": provider, "voice": voice, "rate": rate, "pitch": pitch}
    if provider == "google":
        rate = float(values.get("google_rate", 1.10))
        if not math.isfinite(rate) or not .25 <= rate <= 4:
            raise ValueError("Google 语速倍率应在 0.25 到 4.00 之间。")
        return {"provider": provider, "voice": str(values.get("google_voice") or "cmn-CN-Wavenet-B").strip(),
                "rate": rate, "quota_project": str(values.get("google_quota_project") or "").strip()}
    raise ValueError("请选择已支持的配音来源。")


def normalize(source, target):
    import imageio_ffmpeg
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                             "-i", str(source), "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(target)],
                            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("配音音频无法解码，已停止合成；请检查语音服务返回结果。")


def synthesize(settings, segments, manifest, emit, *, google_confirmed=False):
    provider = settings["provider"]
    manifest.write_text(json.dumps({**settings, "segments": segments}, ensure_ascii=False), encoding="utf-8")
    if provider == "windows":
        emit("正在使用 Windows 本机中文语音生成配音…", 3)
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("speech.ps1")), "-Manifest", str(manifest)],
                                capture_output=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
        manifest.with_suffix(".log").write_bytes(result.stdout+b"\n"+result.stderr)
        if result.returncode:
            detail = result.stderr.decode("utf-8-sig", errors="replace").strip()
            raise RuntimeError("未找到本机中文语音，请在 Windows 语言和语音设置中安装中文语音。" if "VOICE_NOT_FOUND" in detail else detail or "本机配音未完成，请检查 Windows 中文语音。")
        return {**settings, "voice": json.loads(result.stdout.decode("utf-8-sig"))["voice"]}
    if provider == "edge":
        try:
            import edge_tts
        except ImportError:
            raise RuntimeError("Edge 语音组件未安装，请先安装项目更新后的运行依赖。") from None

        async def generate():
            for index, segment in enumerate(segments):
                emit(f"Edge {settings['voice']} 配音 · {index+1}/{len(segments)}", 3+round(17*index/len(segments)))
                source = Path(segment["path"]).with_suffix(".source.mp3")
                try:
                    request = edge_tts.Communicate(segment["text"], settings["voice"],
                                                   rate=f"{settings['rate']:+d}%", pitch=f"{settings['pitch']:+d}Hz")
                    await asyncio.wait_for(request.save(str(source)), timeout=90)
                except Exception as error:
                    raise RuntimeError(f"Edge 配音未完成（{type(error).__name__}），请检查网络和音色；未改用系统声音。") from None
                normalize(source, Path(segment["path"]))
        asyncio.run(generate())
        return settings
    if provider != "google":
        raise ValueError("不支持的配音来源。")
    if google_confirmed is not True:
        raise ValueError("Google Cloud 配音需要在 App 内确认本次计费调用。")
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        from google.auth.exceptions import DefaultCredentialsError
    except ImportError:
        raise RuntimeError("Google 语音组件未安装，请先安装项目更新后的运行依赖。") from None
    try:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"],
                                             quota_project_id=settings.get("quota_project") or None)
    except DefaultCredentialsError:
        raise RuntimeError("尚未配置 Google Cloud 本机授权，请按视频设置里的说明完成官方 ADC 登录。") from None
    with AuthorizedSession(credentials) as session:
        for index, segment in enumerate(segments):
            emit(f"Google {settings['voice']} 配音 · {index+1}/{len(segments)}", 3+round(17*index/len(segments)))
            try:
                response = session.post("https://texttospeech.googleapis.com/v1/text:synthesize",
                                        json={"input": {"text": segment["text"]},
                                              "voice": {"languageCode": "cmn-CN", "name": settings["voice"]},
                                              "audioConfig": {"audioEncoding": "LINEAR16", "speakingRate": settings["rate"], "sampleRateHertz": 24000}},
                                        timeout=(10, 90))
            except Exception:
                raise RuntimeError("Google 配音请求未确认完成。请先检查云端用量；重新生成可能再次计费，本轮未自动重发。") from None
            if response.status_code != 200:
                raise RuntimeError(f"Google 配音返回 HTTP {response.status_code}，请检查授权、项目计费、服务启用状态和音色；未改用其他声音。")
            try:
                audio = base64.b64decode(response.json()["audioContent"], validate=True)
                if not audio:
                    raise ValueError("empty")
            except (ValueError, KeyError, TypeError):
                raise RuntimeError("Google 未返回有效配音，已停止；未自动重新请求。") from None
            source = Path(segment["path"]).with_suffix(".source.wav")
            source.write_bytes(audio)
            normalize(source, Path(segment["path"]))
    return settings
