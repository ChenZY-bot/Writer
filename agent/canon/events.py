"""事件账本：把"防撞梗雷达"从一段自由文本改成可收敛的数据结构。

为什么必须换
------------
旧实现把 `global_events` 当纯文本：

    【已完结的历史大事件（绝对禁止重复）】
    - 第82章起：虚无海封印破损危机 (至第83章完)
    【当前进行中的篇章/事件（允许且应当接续发展）】
    - 第1章起：角色A转入高二B班
    - 第2章起：角色A转学入校
    ...（共 121 条）

结果：写到第 88 章时，"进行中"堆了 121 条、真正"已完结"只有 1 条。
Reviewer 的判据是"只有重演【已完结】事件才打回"，于是这条主关卡在结构上永不触发
（全量日志里 207 次审查只有 10 次打回）。同时这 2400+ 字的表每章要注入
planner 和 reviewer 两遍。

这个模块做四件事
----------------
1. 解析 / 渲染（与旧文本格式双向兼容，可无损迁移）；
2. 去重：同义或包含关系的条目合并（"反派家族武装突袭名门家" 与
   "反派家族武装突袭名门家族总部" 只留一条）；
3. 封顶：`ongoing` 超过预算时，最老的条目转"已归档"，而不是无限膨胀；
4. 生命周期：开启 / 继续 / 结束 / 归档，全部有章节号可追溯。

设计约束
--------
* 纯 stdlib，不依赖项目内任何模块 —— 保证可以离线单测。
* 不静默降级：解析不了的行走 `unparsed` 返回，由调用方记录，不悄悄丢。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 解析

# 例：- 第82章起：虚无海封印破损危机 (至第83章完)
#     - 第29章起：[应对渡边家市郊山间神社天照召灵仪式]
_ENTRY_RE = re.compile(
    r"^[-*]\s*"
    r"(?:第\s*(?P<start>\d+)\s*章起\s*[：:]\s*)?"
    r"(?P<name>.+?)"
    r"\s*(?:[（(]至第\s*(?P<end>\d+)\s*章完[)）])?\s*$"
)

_HEAD_CLOSED = "已完结"
_HEAD_ONGOING = "当前进行中"

# 去重归一化：去掉括号、标点、空白、章节标记
_NORM_STRIP = re.compile(r"[\[\]【】（）()「」『』<>《》:：，,。.、;；!！?？\s\"'“”‘’]")
_CHAPTER_PREFIX = re.compile(r"^第\s*\d+\s*章起?\s*")


def normalize_name(name: str) -> str:
    """把事件名归一化成可比较的键（只用于判重，不改展示文本）。"""
    s = _CHAPTER_PREFIX.sub("", name.strip())
    return _NORM_STRIP.sub("", s).lower()


@dataclass
class Event:
    name: str
    start: int
    end: int | None = None
    archived: bool = False          # 长期未推进，被预算挤到"已归档"

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        d = {"name": self.name, "start": self.start}
        if self.end is not None:
            d["end"] = self.end
        if self.archived:
            d["archived"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(
            name=str(d.get("name", "")).strip(),
            start=int(d.get("start", 0) or 0),
            end=(int(d["end"]) if d.get("end") is not None else None),
            archived=bool(d.get("archived", False)),
        )

    @property
    def is_open(self) -> bool:
        return self.end is None

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    def render(self) -> str:
        prefix = f"第{self.start}章起：" if self.start else ""
        tail = f" (至第{self.end}章完)" if self.end is not None else ""
        if self.archived:
            tail += " [长期未推进，已归档]"
        return f"- {prefix}{self.name}{tail}"


@dataclass
class EventLedger:
    events: list[Event] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ 解析 / 渲染

    @classmethod
    def from_text(cls, text: str) -> "EventLedger":
        """支持三种输入：空、旧版纯文本、新版 JSON。"""
        ledger = cls()
        text = (text or "").strip()
        if not text or text == "无":
            return ledger

        if text.startswith("{"):                     # 新版 JSON
            try:
                data = json.loads(text)
                for item in data.get("events", []):
                    ev = Event.from_dict(item)
                    if ev.name:
                        ledger.events.append(ev)
                return ledger
            except (ValueError, TypeError, AttributeError):
                # 落到文本解析，而不是直接抛：存档坏掉时仍要能续写
                ledger.unparsed.append("<JSON 解析失败，已回退文本模式>")

        section = "ongoing"      # 没有标题的裸条目按"进行中"处理，避免被误判成已完结
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("【"):
                section = "ongoing" if _HEAD_ONGOING in line else "closed"
                continue
            if not line.startswith(("-", "*")):
                continue
            m = _ENTRY_RE.match(line)
            if not m or not m.group("name"):
                ledger.unparsed.append(line)
                continue
            start = int(m.group("start")) if m.group("start") else 0
            end = int(m.group("end")) if m.group("end") else None
            if section == "closed" and end is None:
                end = start or 0            # 旧存档里已完结条目偶尔缺结束章
            ledger.events.append(Event(name=m.group("name").strip(), start=start, end=end))
        return ledger

    def to_json(self) -> str:
        return json.dumps(
            {"version": 2, "events": [e.to_dict() for e in self.events]},
            ensure_ascii=False,
            indent=2,
        )

    def to_text(self, max_closed: int = 40, max_archived_chars: int = 500) -> str:
        """渲染成 prompt 可用的文本。

        预算控制的两层：
          * 已完结事件最多列 `max_closed` 条（按结束章取最近的）；
          * 归档事件不逐条展开，压成一行"禁止复用"的名字清单（默认 500 字上限）。
        完整数据始终在 `to_json()` 里，prompt 里只放摘要 —— 这是 fish "注入预算"的做法。
        """
        closed = sorted(
            (e for e in self.events if not e.is_open and not e.archived),
            key=lambda e: (e.end or 0, e.start),
        )[-max_closed:]
        archived = sorted((e for e in self.events if e.archived), key=lambda e: (e.end or 0, e.start))
        ongoing = sorted((e for e in self.events if e.is_open), key=lambda e: e.start)

        lines: list[str] = []
        if closed:
            lines.append("【已完结的历史大事件（绝对禁止重复）】")
            lines.extend(e.render() for e in closed)
        if archived:
            joined = "、".join(e.name for e in archived)
            if len(joined) > max_archived_chars:
                joined = joined[:max_archived_chars] + f"…（共 {len(archived)} 条，明细见 events_json）"
            lines.append("")
            lines.append("【已归档：长期未推进，禁止再作为核心桥段复用】")
            lines.append("- " + joined)
        if ongoing:
            lines.append("")
            lines.append("【当前进行中的篇章/事件（允许且应当接续发展）】")
            lines.extend(e.render() for e in ongoing)
        if not closed and not archived and not ongoing:
            lines.append("【当前进行中的篇章/事件】\n暂无 (当前为日常过渡)")
        return "\n".join(lines).strip()

    # ------------------------------------------------------------ 去重

    def dedupe(self) -> int:
        """合并同义/包含关系条目，返回合并掉的条数。

        规则（从严到宽）：
          1. 归一化后完全相同 -> 合并；
          2. 一方是另一方的子串、且较短者 >= 6 字 -> 合并（保留更长、更具体的名字）；
          3. 合并时：起始章取更早，结束章取更晚；任一为开启态则保持开启。
        """
        merged = 0
        result: list[Event] = []
        for ev in sorted(self.events, key=lambda e: (e.start or 0, -len(e.name))):
            key = ev.key
            if not key:
                continue
            target = None
            for kept in result:
                kk = kept.key
                if not kk:
                    continue
                if key == kk or (len(key) >= 6 and key in kk) or (len(kk) >= 6 and kk in key):
                    target = kept
                    break
            if target is None:
                result.append(ev)
                continue
            merged += 1
            if len(ev.name) > len(target.name):
                target.name = ev.name
            starts = [x for x in (target.start, ev.start) if x]
            target.start = min(starts) if starts else 0
            if target.end is None or ev.end is None:
                target.end = None
            else:
                target.end = max(target.end, ev.end)
            target.archived = target.archived and ev.archived
        self.events = result
        return merged

    # ------------------------------------------------------------ 生命周期

    def _find_open(self, name: str) -> Event | None:
        key = normalize_name(name)
        if not key:
            return None
        for ev in self.events:
            if not ev.is_open:
                continue
            kk = ev.key
            if key == kk or key in kk or kk in key:
                return ev
        return None

    def open_event(self, name: str, chapter: int) -> Event:
        """开启事件；已存在同义开启态则复用它（幂等）。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("事件名不能为空")
        existing = self._find_open(name)
        if existing:
            return existing
        ev = Event(name=name, start=chapter)
        self.events.append(ev)
        return ev

    def close_event(self, name: str, chapter: int) -> Event | None:
        """结束事件；找不到返回 None（调用方据此告警，而不是静默造一条）。"""
        ev = self._find_open(name)
        if ev is None:
            return None
        ev.end = chapter
        return ev

    def apply_tags(self, tag_text: str, chapter: int) -> dict[str, list[str]]:
        """解析记忆 Agent 输出的四类状态指令。

        支持（可多行、可并行）：
          开启新事件：学园祭
          事件继续：学园祭
          事件结束：学园祭
          日常过渡
        返回 {"opened": [...], "closed": [...], "continued": [...], "unknown": [...]}
        """
        out: dict[str, list[str]] = {"opened": [], "closed": [], "continued": [], "unknown": []}
        for raw in (tag_text or "").splitlines():
            line = raw.strip().lstrip("-*").strip()
            if not line or line == "无":
                continue
            if "开启新事件" in line or "新事件开启" in line:
                name = re.sub(r"^.*?(?:开启新事件|新事件开启)[：:]?\s*", "", line).strip()
                if name:
                    self.open_event(name, chapter)
                    out["opened"].append(name)
            elif "事件结束" in line:
                name = re.sub(r"^.*?事件结束[：:]?\s*", "", line).strip()
                if not name:
                    # 没写名字：退化处理要显式记录，不假装成功
                    opens = [e for e in self.events if e.is_open]
                    if opens:
                        ev = min(opens, key=lambda e: e.start)
                        ev.end = chapter
                        out["closed"].append(ev.name)
                    else:
                        out["unknown"].append(line)
                elif self.close_event(name, chapter) is None:
                    out["unknown"].append(line)
                else:
                    out["closed"].append(name)
            elif "事件继续" in line:
                name = re.sub(r"^.*?事件继续[：:]?\s*", "", line).strip()
                if name:
                    out["continued"].append(name)
            elif "日常过渡" in line:
                continue
            else:
                out["unknown"].append(line)
        return out

    # ------------------------------------------------------------ 预算

    def enforce_budget(self, current_chapter: int, max_ongoing: int = 15) -> list[Event]:
        """超出 `max_ongoing` 时归档最老的条目，返回被归档的事件。

        归档 ≠ 删除：它们仍出现在"已完结"区块里，继续参与防撞梗，
        只是不再占用"进行中"的注意力预算。
        """
        archived: list[Event] = []
        ongoing = sorted((e for e in self.events if e.is_open), key=lambda e: e.start)
        overflow = len(ongoing) - max(0, max_ongoing)
        if overflow <= 0:
            return archived
        for ev in ongoing[:overflow]:
            ev.end = current_chapter
            ev.archived = True
            archived.append(ev)
        return archived

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self.events),
            "open": sum(1 for e in self.events if e.is_open),
            "closed": sum(1 for e in self.events if not e.is_open),
            "archived": sum(1 for e in self.events if e.archived),
        }
