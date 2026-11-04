"""全局状态定义（唯一真源）。

原先 AgentState 在 workflow_graph / planner / writer / reviewer / memory 五个文件里
各定义一份，字段已经漂移（writer 缺 global_events、reviewer 缺 director_notes）。
TypedDict 在运行时不校验，所以漂移不会报错，只会让后续维护者写错字段名。

这里用 total=False：允许"只更新部分字段"，也允许渐进式加字段而不动五个节点文件。
"""
from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # ---- 原始字段（保持兼容） ----
    outline: str                 # 总体大纲
    director_notes: str          # 场外导演 / 人工干预指令（main 每章注入）
    current_chapter_num: int     # 当前章节号
    context_memory: str          # 最近 N 章的压缩记忆（滑动窗口）
    previous_tail: str           # 上一章结尾原文锚点
    global_events: str           # 旧版：事件表纯文本（保留，供迁移与回滚）
    chapter_plan: str            # 本章细纲
    draft_content: str           # 本章草稿
    feedback: str                # 审稿/闸门反馈
    revision_count: int          # 重写次数

    # ---- 新增字段 ----
    events_json: str             # 新版：事件账本 JSON（EventLedger 序列化）
    applied_directives: list[str]  # 本章已消化的人工指令 id，用于幂等与审计
    quality_issues: list[dict[str, Any]]  # 客观闸门产出的问题清单


# 各节点函数返回的 dict 就是"部分状态更新"，与 AgentState 同构。
StateUpdate = dict[str, Any]
