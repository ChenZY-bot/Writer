"""伏笔账本：让"挖坑"和"填坑"变成可对账的两条记录。

为什么需要它
------------
现在系统里没有任何"我埋过什么、答应什么时候还"的记录：

* `global_events` 只记大事件，不记伏笔；
* `context_memory` 只保留最近 5 章摘要，第 12 章埋的钩子到第 40 章早就被淘汰；
* 长程检索是"相似度 top-8"，问的是"和本章细纲像不像"，不是"哪些坑该填了"。

于是"伏笔自圆其说"完全依赖模型自觉。这个账本把伏笔变成显式条目：
每条都有**埋设章、承诺回收章、状态、回收方式**，逾期会被顶到写手面前。

与事件账本的分工
----------------
* 事件（EventLedger）："故事里发生过什么"——用于防撞梗、防复读。
* 伏笔（ForeshadowLedger）："故事还欠读者什么"——用于收线与自圆其说。
两者共享 `memory/` 目录，但互不依赖。

纯 stdlib；JSON 落盘；所有写操作先写临时文件再原子替换，避免半截文件。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Literal

Status = Literal["open", "paid", "dropped"]


@dataclass
class Thread:
    id: str
    title: str
    planted: int                 # 埋设章
    due: int | None = None       # 承诺回收章（None = 未定期限）
    status: Status = "open"
    paid_chapter: int | None = None
    evidence: str = ""           # 埋设原文片段（对账用）
    payoff: str = ""             # 回收方式（填了才算真的"自圆"）
    owner: str = ""              # 关联角色 / 线索归属
    priority: int = 2            # 1 高 2 中 3 低

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "planted": self.planted,
            "due": self.due, "status": self.status,
            "paid_chapter": self.paid_chapter, "evidence": self.evidence,
            "payoff": self.payoff, "owner": self.owner, "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Thread":
        status = str(d.get("status", "open"))
        if status not in ("open", "paid", "dropped"):
            status = "open"
        return cls(
            id=str(d.get("id", "")),
            title=str(d.get("title", "")).strip(),
            planted=int(d.get("planted", 0) or 0),
            due=(int(d["due"]) if d.get("due") is not None else None),
            status=status,  # type: ignore[arg-type]
            paid_chapter=(int(d["paid_chapter"]) if d.get("paid_chapter") is not None else None),
            evidence=str(d.get("evidence", "")),
            payoff=str(d.get("payoff", "")),
            owner=str(d.get("owner", "")),
            priority=int(d.get("priority", 2) or 2),
        )

    def overdue(self, current: int) -> int:
        """逾期章数；未逾期返回 0。"""
        if self.status != "open" or self.due is None:
            return 0
        return max(0, current - self.due)

    def render(self, current: int) -> str:
        bits = [f"{self.id}《{self.title}》"]
        bits.append(f"第{self.planted}章埋设")
        if self.due is not None:
            od = self.overdue(current)
            bits.append(f"承诺第{self.due}章前回收" + (f"，已逾期{od}章" if od else "，本章到期"))
        if self.evidence:
            bits.append(f"埋设原文：{self.evidence[:60]}")
        if self.owner:
            bits.append(f"关联：{self.owner}")
        return "- " + "；".join(bits)


@dataclass
class ForeshadowLedger:
    threads: list[Thread] = field(default_factory=list)
    path: str | None = None

    # ------------------------------------------------------------ 持久化

    @classmethod
    def load(cls, path: str) -> "ForeshadowLedger":
        ledger = cls(path=path)
        if not os.path.exists(path):
            return ledger
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return ledger
        for item in data.get("threads", []) if isinstance(data, dict) else []:
            t = Thread.from_dict(item)
            if t.id and t.title:
                ledger.threads.append(t)
        return ledger

    def save(self) -> None:
        if not self.path:
            raise ValueError("未指定 path，无法保存")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = json.dumps(
            {"version": 1, "threads": [t.to_dict() for t in self.threads]},
            ensure_ascii=False, indent=2,
        )
        # 原子写：先同目录临时文件，再 os.replace
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ------------------------------------------------------------ 增删改

    def _next_id(self, chapter: int) -> str:
        n = sum(1 for t in self.threads if t.planted == chapter) + 1
        return f"FS-{chapter:03d}-{n:02d}"

    def add(self, title: str, planted: int, *, due: int | None = None,
            evidence: str = "", owner: str = "", priority: int = 2,
            thread_id: str | None = None) -> Thread:
        title = (title or "").strip()
        if not title:
            raise ValueError("伏笔标题不能为空")
        t = Thread(id=thread_id or self._next_id(planted), title=title, planted=planted,
                   due=due, evidence=evidence, owner=owner, priority=priority)
        self.threads.append(t)
        return t

    def find(self, key: str) -> Thread | None:
        key = (key or "").strip()
        for t in self.threads:
            if t.id == key or t.title == key or (len(key) >= 4 and key in t.title):
                return t
        return None

    def pay(self, key: str, chapter: int, payoff: str = "") -> Thread | None:
        t = self.find(key)
        if t is None or t.status != "open":
            return None
        t.status = "paid"
        t.paid_chapter = chapter
        if payoff:
            t.payoff = payoff
        return t

    def drop(self, key: str, reason: str = "") -> Thread | None:
        t = self.find(key)
        if t is None or t.status != "open":
            return None
        t.status = "dropped"
        if reason:
            t.payoff = f"放弃：{reason}"
        return t

    # ------------------------------------------------------------ 查询

    def due(self, current: int) -> list[Thread]:
        """本章到期或已逾期的开放伏笔，按逾期程度与优先级排序。"""
        rows = [t for t in self.threads if t.status == "open" and t.due is not None and t.due <= current]
        rows.sort(key=lambda t: (-t.overdue(current), t.priority, t.planted))
        return rows

    def open_threads(self) -> list[Thread]:
        return [t for t in self.threads if t.status == "open"]

    def render_block(self, current: int, max_items: int = 6, max_chars: int = 900) -> str:
        """渲染成可注入 prompt 的文本块；空账本返回空串（调用方无需特判）。"""
        rows = self.due(current)
        if not rows:
            return ""
        lines = ["【伏笔账本·本章必须处理】"]
        used = len(lines[0])
        listed = 0
        for t in rows:
            if listed >= max_items:
                break
            line = t.render(current)
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line)
            listed += 1
        if listed < len(rows):
            lines.append(f"- 另有 {len(rows) - listed} 条到期伏笔未列出")
        lines.append("处理方式：本章回收的，必须在正文中明确交代；本章不回收的，必须在细纲里写明推迟理由与新的回收章。")
        return "\n".join(lines)

    def stats(self, current: int) -> dict[str, int]:
        return {
            "total": len(self.threads),
            "open": len(self.open_threads()),
            "due": len(self.due(current)),
            "paid": sum(1 for t in self.threads if t.status == "paid"),
            "dropped": sum(1 for t in self.threads if t.status == "dropped"),
        }


if __name__ == "__main__":  # 人工维护入口（HITL 可直接用，不必开 UI）
    import argparse

    ap = argparse.ArgumentParser(description="伏笔账本 CLI")
    ap.add_argument("--path", default=os.path.join("memory", "foreshadow.json"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="登记一条伏笔")
    p_add.add_argument("title")
    p_add.add_argument("--planted", type=int, required=True)
    p_add.add_argument("--due", type=int)
    p_add.add_argument("--owner", default="")
    p_add.add_argument("--evidence", default="")
    p_add.add_argument("--priority", type=int, default=2)

    p_pay = sub.add_parser("pay", help="标记已回收")
    p_pay.add_argument("key")
    p_pay.add_argument("--chapter", type=int, required=True)
    p_pay.add_argument("--payoff", default="")

    p_drop = sub.add_parser("drop", help="放弃一条伏笔")
    p_drop.add_argument("key")
    p_drop.add_argument("--reason", default="")

    sub.add_parser("list", help="列出全部")
    p_due = sub.add_parser("due", help="列出某章到期")
    p_due.add_argument("--chapter", type=int, required=True)

    args = ap.parse_args()
    led = ForeshadowLedger.load(args.path)

    if args.cmd == "add":
        t = led.add(args.title, args.planted, due=args.due, owner=args.owner,
                    evidence=args.evidence, priority=args.priority)
        led.save()
        print(f"已登记 {t.id}《{t.title}》")
    elif args.cmd == "pay":
        t = led.pay(args.key, args.chapter, args.payoff)
        if t is None:
            raise SystemExit(f"未找到开放伏笔：{args.key}")
        led.save()
        print(f"已回收 {t.id}《{t.title}》")
    elif args.cmd == "drop":
        t = led.drop(args.key, args.reason)
        if t is None:
            raise SystemExit(f"未找到开放伏笔：{args.key}")
        led.save()
        print(f"已放弃 {t.id}《{t.title}》")
    elif args.cmd == "list":
        for t in led.threads:
            print(f"[{t.status:7}] {t.render(0)}")
        print(json.dumps(led.stats(0), ensure_ascii=False))
    elif args.cmd == "due":
        print(led.render_block(args.chapter) or "（本章无到期伏笔）")
