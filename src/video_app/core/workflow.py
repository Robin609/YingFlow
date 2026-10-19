"""Workflow contracts shared by persistence, UI and future provider adapters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .contracts import TextRequest, TextResult


@dataclass(frozen=True)
class Stage:
    key: str
    title: str
    provider: str
    dependencies: tuple[str, ...]
    description: str


STAGES = (
    Stage("brief", "项目要求", "local", (), "领域、受众、时长与画幅"),
    Stage("topic", "选题", "antigravity", ("brief",), "确定标题与切入角度"),
    Stage("draft", "初稿", "antigravity", ("topic",), "旁白正文与讲解结构"),
    Stage("review", "审核", "gemini_web", ("draft",), "审核意见与修订后的完整正文"),
    Stage("final", "终稿", "local", ("review",), "可选 GPT 润色，人工确认版本"),
    Stage("storyboard", "分镜", "local", ("final",), "镜头、旁白片段与画面方案"),
    Stage("visuals", "画面", "video", ("storyboard",), "程序化动画或视频素材"),
    Stage("audio", "配音", "audio", ("final",), "导入配音或接入语音服务"),
    Stage("export", "导出", "local", ("final", "storyboard"), "文稿、分镜与项目记录"),
)
STAGE_MAP = {stage.key: stage for stage in STAGES}
PROVIDERS = {
    "local": "本地 / 人工",
    "antigravity": "Antigravity",
    "gemini_web": "Gemini 网页",
    "codex": "GPT / Codex",
    "video": "画面服务",
    "audio": "语音服务",
}
STATE_NAMES = {
    "PENDING": "待开始", "RUNNING": "执行中", "WAITING_USER": "待你处理",
    "BLOCKED": "待配置", "SUCCEEDED": "已确认", "FAILED": "失败",
    "CANCELLED": "已取消", "STALE": "需更新", "SKIPPED": "本轮不需要",
    "UNKNOWN": "结果待核对",
}


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class NodeProvider(Protocol):
    """Transport only; core owns validation, publication and run identity."""

    async def generate(self, request: TextRequest, emit: Callable[[dict], None]) -> TextResult: ...
    async def cancel(self, run_id: str) -> dict: ...


def dependencies(stage: str, job_kind: str) -> tuple[str, ...]:
    if stage == "export" and job_kind == "full_video":
        return ("final", "storyboard", "visuals", "audio")
    return STAGE_MAP[stage].dependencies


def applicable(stage: str, job_kind: str) -> bool:
    return job_kind == "full_video" or stage not in {"visuals", "audio"}
