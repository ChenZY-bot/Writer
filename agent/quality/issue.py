"""质量问题的最小载体：一个 issue 就是一条可执行的修改要求。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Issue:
    code: str            # 机器可读，如 G-REPEAT-B
    severity: str        # "hard"（必须重写）｜ "warn"（提示，不阻塞）
    message: str         # 直接拼进审稿反馈的中文说明
    evidence: str = ""   # 证据片段（截断），便于人工复核

    @property
    def hard(self) -> bool:
        return self.severity == "hard"

    def render(self) -> str:
        tag = "【必须改】" if self.hard else "【建议改】"
        ev = f"（证据：{self.evidence}）" if self.evidence else ""
        return f"{tag}{self.message}{ev}"
