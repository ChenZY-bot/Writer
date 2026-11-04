import os
import sys
import json
import time
import re
import shutil
from datetime import datetime

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.workflow_graph import app, AgentState
from utils.logger_handler import sys_logger
from utils.config_handler import settings_config
from utils.file_handler import save_chapter, get_output_dir
from agent.quality.gate import normalize_chapter
from agent.canon.foreshadow import ForeshadowLedger
from agent.hitl.directives import (
    CanonStore, apply_directives, load_pending, mark_applied,
)

STATE_FILE = os.path.join(project_root, "memory", "state.json")
OUTLINE_FILE = os.path.join(project_root, "config", "outline.txt")
NOTES_FILE = os.path.join(project_root, "config", "director_notes.txt")
FORESHADOW_FILE = os.path.join(project_root, "memory", "foreshadow.json")
GLOSSARY_FILE = os.path.join(project_root, "memory", "glossary.json")
BANNED_FILE = os.path.join(project_root, "memory", "banned_motifs.json")
DIRECTIVE_DIR = os.path.join(project_root, "config", "directives")
INBOX_FILE = os.path.join(DIRECTIVE_DIR, "inbox.jsonl")
APPLIED_FILE = os.path.join(DIRECTIVE_DIR, "applied.jsonl")


def _features() -> dict:
    return settings_config.get("features") or {}


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            sys_logger.info(f"成功读取断点存档：准备从第 {state.get('current_chapter_num', 1)} 章开始续写。")
            return state
        except Exception as e:
            sys_logger.error(f"读取存档失败: {e}")
    return {
        "current_chapter_num": 1,
        "context_memory": "无",
        "previous_tail": "无（这是小说的开头，暂无前文）。",
        "global_events": "无",
        "events_json": "",
        "chapter_plan": "",
        "draft_content": "",
        "feedback": "",
        "revision_count": 0
    }


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    clean_state = {
        "current_chapter_num": state["current_chapter_num"],
        "context_memory": state["context_memory"],
        "previous_tail": state.get("previous_tail", "无"),
        "global_events": state.get("global_events", "无"),
        "events_json": state.get("events_json", ""),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(clean_state, f, ensure_ascii=False, indent=4)
    sys_logger.debug("断点存档已更新。")


def get_file_content(filepath: str, create_if_missing: bool = False, default_text: str = "") -> str:
    if not os.path.exists(filepath):
        if create_if_missing:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(default_text)
        return default_text
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read().strip()


def clear_director_notes():
    if os.path.exists(NOTES_FILE):
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            f.write("")


# ==========================================
# 人工干预：每章重新读一次 inbox（热更新）
# ==========================================
def apply_pending_directives(chapter_num: int, canon: CanonStore) -> tuple[str, list[str], int | None]:
    """返回 (注入文本, 已生效指令 id, 回滚章号)。"""
    if not _features().get("directives", True):
        return "", [], None

    pending, errors = load_pending(INBOX_FILE, APPLIED_FILE)
    for err in errors:
        sys_logger.error(f"[指令] {err}")
    if not pending:
        return "", [], None

    result = apply_directives(pending, chapter=chapter_num, canon=canon)
    if result.consumed:
        canon.save()
        mark_applied(APPLIED_FILE, [d for d in pending if d.id in result.consumed], chapter_num)
        sys_logger.info(f"[指令] 本章生效 {len(result.consumed)} 条：{result.consumed}")
    for err in result.errors:
        sys_logger.error(f"[指令] {err}")
    return result.render_notes(), result.consumed, result.rewind_chapter


def build_chapter_context(chapter_num: int, user_notes: str, canon: CanonStore,
                          directive_notes: str) -> str:
    """把四类材料拼成 director_notes——planner 和 writer 都已消费这个字段，
    所以不需要改任何 prompt 模板。"""
    blocks: list[str] = []
    if user_notes:
        blocks.append("【场外导演临时指导/灵感】\n" + user_notes)
    if directive_notes:
        blocks.append("【人工干预指令（本章生效）】\n" + directive_notes)
    canon_block = canon.render_block()
    if canon_block:
        blocks.append(canon_block)
    if _features().get("foreshadow_block", True):
        block = ForeshadowLedger.load(FORESHADOW_FILE).render_block(chapter_num)
        if block:
            blocks.append(block)
    if canon.banned:
        blocks.append("【禁止复用的桥段/场景】\n" + "、".join(f"「{m}」" for m in canon.banned)
                      + "\n(这些桥段已经写过，本章及以后不得再作为核心场景出现)")
    return "\n\n".join(blocks)


def archive_from(chapter_num: int) -> int:
    """执行 rewrite_chapter：把第 N 章及之后归档到 output/_archive_时间戳，返回新的起始章号。

    注意（如实说明）：只回滚**正文文件与章节号**，不回滚向量库和 events_json——
    那需要重建索引。归档而不是删除，是为了让被作废的稿子还能被人工翻查。
    """
    output_dir = get_output_dir()
    archive_dir = os.path.join(output_dir, f"_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(archive_dir, exist_ok=True)
    moved = 0
    for name in os.listdir(output_dir):
        m = re.fullmatch(r"chapter_(\d{3})\.txt", name)
        if m and int(m.group(1)) >= chapter_num:
            shutil.move(os.path.join(output_dir, name), os.path.join(archive_dir, name))
            moved += 1
    sys_logger.warning(
        f"[回滚] 第 {chapter_num} 章及之后的 {moved} 个文件已归档到 {archive_dir}；"
        "向量库与事件账本不会自动回滚，如需一致请手动重建。"
    )
    return chapter_num


def clean_tail(draft: str) -> str:
    """斩断大模型乱加的"本章完"与字数统计，并取结尾 300 字作为物理锚点。"""
    clean_draft = re.split(
        r'(\(本章完\)|（本章完）|下章预告|未完待续|本章完|（全文.*?字）|\(全文.*?字\)|全文\d+字)',
        draft,
    )[0].strip()
    if len(clean_draft) > 300:
        return "..." + clean_draft[-300:].strip()
    return clean_draft.strip()


def main():
    sys_logger.info("\n" + "="*50 + "\n大文豪 Agent 系统启动！\n" + "="*50)
    target_chapters = settings_config.get("default_generate_chapters", 3)
    current_state = load_state()
    canon = CanonStore.load(GLOSSARY_FILE, BANNED_FILE)

    for i in range(target_chapters):
        chap_num = current_state["current_chapter_num"]

        # 每章重新读一次 inbox：流水线在跑的时候写文件，下一章就生效（热更新）
        directive_notes, applied_ids, rewind = apply_pending_directives(chap_num, canon)
        if rewind is not None:
            chap_num = archive_from(rewind)
            current_state["current_chapter_num"] = chap_num
            directive_notes += f"\n- 已回滚到第 {chap_num} 章：之前被作废的剧情不得再延续。"

        sys_logger.info(f"\n>>> 开始全自动生产第 {chap_num} 章 <<<")

        outline_text = get_file_content(OUTLINE_FILE, True, "请填写大纲...")
        if not outline_text or outline_text == "请填写大纲...":
            sys_logger.error("大纲为空，中止！")
            return

        user_notes = get_file_content(NOTES_FILE, True, "")
        injection = build_chapter_context(chap_num, user_notes, canon, directive_notes)

        current_state.update({
            "outline": outline_text,
            "director_notes": injection,
            "chapter_plan": "",
            "draft_content": "",
            "feedback": "",
            "revision_count": 0,
            "applied_directives": applied_ids,
        })

        try:
            final_state = app.invoke(current_state)
        except Exception as e:
            sys_logger.error(f"第 {chap_num} 章生成过程致命错误：{e}")
            break

        if final_state.get("feedback"):
            sys_logger.error(f"第 {chap_num} 章未能通过审核，已达最大重试次数。任务中止！")
            break

        draft = final_state.get("draft_content", "")
        if _features().get("normalize_output", True):
            norm = normalize_chapter(draft, chap_num)
            if norm.fixes:
                sys_logger.info(f"落盘前格式修正：{'、'.join(norm.fixes)}")
            draft = norm.text
        save_chapter(chap_num, draft)

        if user_notes:
            clear_director_notes()

        # 工作记忆 / 事件账本
        current_state["context_memory"] = final_state.get("context_memory", "")
        current_state["global_events"] = final_state.get(
            "global_events", current_state.get("global_events", "无"))
        current_state["events_json"] = final_state.get(
            "events_json", current_state.get("events_json", ""))

        current_state["previous_tail"] = clean_tail(draft)
        current_state["current_chapter_num"] += 1
        save_state(current_state)

        time.sleep(3)

    sys_logger.info("\n" + "="*50 + "\n任务圆满结束！\n" + "="*50)


if __name__ == "__main__":
    main()
