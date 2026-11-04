"""Gate 节点：写手 -> 【客观闸门】 -> 审稿人。

放在写手和审稿人之间，理由：

1. 能机器判的东西不必花 token 让 LLM 判（而且它判不准：207 次审查只打回 10 次）；
2. 闸门先拦一道，审稿人只需要评"文笔/人物/OOC"这类主观项，职责变清楚；
3. 闸门的反馈是**可复现的字符串**，重写方向明确，不会像 LLM 反馈那样每次不一样。

模式（`config/settings_config.yml` 的 `features.quality_gate`）
------------------------------------------------------------
  off      什么都不做（默认，零回归）
  dry      只计算并写日志，不拦截（先观察一轮用这个）
  enforce  硬性问题直接打回写手重写

`features.normalize_output`（默认 true）只修格式：去元文本、去标题里重复的章节号、
合并多余空行——不改内容，所以可以默认开。
"""
from __future__ import annotations

import json
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.quality.gate import check_chapter, gate_update, normalize_chapter
from utils.config_handler import settings_config
from utils.file_handler import get_output_dir
from utils.logger_handler import get_logger

gate_logger = get_logger("Gate")


def _features() -> dict:
    return settings_config.get("features") or {}


def _recent_texts(chapter_num: int, limit: int = 2) -> list[str]:
    """读最近几章正文，用于章级查重。倒序：最近的在最前。"""
    out: list[str] = []
    base = get_output_dir()
    for n in range(chapter_num - 1, max(0, chapter_num - 1 - limit), -1):
        path = os.path.join(base, f"chapter_{n:03d}.txt")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    out.append(f.read())
            except OSError as e:
                gate_logger.warning(f"读取第 {n} 章失败，跳过查重：{e}")
    return out


def _load_banned() -> list[str]:
    path = os.path.join(project_root, "memory", "banned_motifs.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(json.load(f).get("motifs") or [])
    except (OSError, ValueError) as e:
        gate_logger.warning(f"读取禁写桥段失败：{e}")
        return []


def gate_node(state: dict) -> dict:
    draft = state.get("draft_content", "") or ""
    chapter_num = state.get("current_chapter_num", 1)
    feats = _features()
    mode = str(feats.get("quality_gate", "off")).lower()
    update: dict = {}

    # ---- 1. 格式归一化（不改变剧情，默认开） ----
    if feats.get("normalize_output", True) and draft:
        norm = normalize_chapter(draft, chapter_num)
        if norm.fixes:
            gate_logger.info(f"第 {chapter_num} 章格式修正：{'、'.join(norm.fixes)}")
        if norm.text != draft:
            draft = norm.text
            update["draft_content"] = draft

    if mode == "off" or not draft:
        # 闸门放行时必须清空 feedback：否则上一轮被打回的 feedback 会一直留在
        # state 里，route_gate_result 每一轮都判"failed"，直到 max_retries 才停。
        update["feedback"] = ""
        return update

    # ---- 2. 客观判据 ----
    target = int(settings_config.get("target_word_count", 2500) or 2500)
    issues, skipped = check_chapter(
        draft,
        chapter_num=chapter_num,
        target_words=target,
        anchor_tail=state.get("previous_tail", ""),
        recent_texts=_recent_texts(chapter_num),
        banned_motifs=_load_banned(),
    )

    update["quality_issues"] = [
        {"code": i.code, "severity": i.severity, "message": i.message} for i in issues
    ]
    hard = [i for i in issues if i.hard]
    warn = [i for i in issues if not i.hard]
    for i in warn:
        gate_logger.warning(f"[{i.code}] {i.message}")
    for i in hard:
        gate_logger.error(f"[{i.code}] {i.message}")
    if skipped:
        gate_logger.info(f"未执行的检查：{'；'.join(skipped)}")

    decision = gate_update(issues, mode=mode,
                           revision_count=int(state.get("revision_count", 0)))
    update.update(decision)

    if decision.get("feedback"):
        gate_logger.warning(
            f"第 {chapter_num} 章被闸门打回（第 {decision['revision_count']} 次重写）。")
    elif mode == "dry":
        gate_logger.info(f"第 {chapter_num} 章闸门（dry）：{len(hard)} 项硬性、{len(warn)} 项提示，不拦截。")
    else:
        gate_logger.info(f"第 {chapter_num} 章闸门通过（{len(warn)} 项提示）。")
    return update
