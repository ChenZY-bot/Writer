"""人工干预收件箱：让"热更新"变成一次文件写入，而不是改代码重跑。

现状
----
`config/director_notes.txt` 是唯一的人工入口：一段自由文本，被 main 读走、拼到
outline 末尾、写完一章就清空。它有三个硬伤：

1. **不可结构化**：想"插入一个角色并让他在第 3 章出场"，只能祈求模型读懂；
2. **不持久**：清空之后，下一章模型又忘了这个角色存在（设定没有落到 canon）；
3. **不可审计**：改过什么、哪章生效、影响哪些章，没有任何记录。

这个模块给出一份**带类型的指令协议**，支持：

  insert_character  插入角色（写进 canon，每章都注入，永不丢）
  lock_fact         锁定事实（例如"主角不能飞"， reviewer 也要看得到）
  steer_plot        一次性剧情纠偏（只作用于下一章，用完即焚）
  ban_motif         禁写桥段（"学园祭"写过了，别再写）
  rewrite_chapter   回滚重写（把第 N 章及之后作废重来，需 confirm=true）

热更新怎么做到
--------------
main 每章开头**重新读一次** inbox。所以流水线在跑的时候，你往
`config/directives/inbox.jsonl` 追加一行，下一章就生效，不用重启进程。
已生效的指令记到 `applied.jsonl`，按 id 幂等，重复投放不会重复执行。

格式（一行一个 JSON）
--------------------
{"id":"D-001","type":"insert_character","payload":{"name":"新角色","setting":"山间神社巫女"}}
{"id":"D-002","type":"ban_motif","payload":{"motif":"学园祭"}}
{"id":"D-003","type":"rewrite_chapter","payload":{"chapter":88,"confirm":true},"note":"第88章人物 OOC"}

纯 stdlib。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime

VALID_TYPES = {
    "insert_character", "lock_fact", "steer_plot",
    "ban_motif", "rewrite_chapter",
}


@dataclass
class Directive:
    id: str
    type: str
    payload: dict = field(default_factory=dict)
    note: str = ""
    created: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "payload": self.payload,
                "note": self.note, "created": self.created}

    @classmethod
    def from_dict(cls, d: dict) -> "Directive":
        return cls(
            id=str(d.get("id", "")).strip(),
            type=str(d.get("type", "")).strip(),
            payload=dict(d.get("payload") or {}),
            note=str(d.get("note", "")),
            created=str(d.get("created", "")),
        )

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.id:
            errs.append("缺少 id")
        if self.type not in VALID_TYPES:
            errs.append(f"未知 type: {self.type}")
        if self.type == "insert_character" and not self.payload.get("name"):
            errs.append("insert_character 需要 payload.name")
        if self.type == "lock_fact" and not (self.payload.get("text") or self.payload.get("fact")):
            errs.append("lock_fact 需要 payload.text")
        if self.type == "steer_plot" and not self.payload.get("text"):
            errs.append("steer_plot 需要 payload.text")
        if self.type == "ban_motif" and not self.payload.get("motif"):
            errs.append("ban_motif 需要 payload.motif")
        if self.type == "rewrite_chapter":
            ch = self.payload.get("chapter")
            if not isinstance(ch, int) or ch < 1:
                errs.append("rewrite_chapter 需要 payload.chapter（正整数）")
            if self.payload.get("confirm") is not True:
                errs.append("rewrite_chapter 需要 payload.confirm=true（防误触发）")
        return errs


@dataclass
class CanonStore:
    """canon = 用户钉死的设定。与 LLM 自动记忆分开，避免被摘要淘汰或改错。"""
    glossary_path: str
    banned_path: str
    characters: dict[str, str] = field(default_factory=dict)
    facts: list[dict] = field(default_factory=list)
    banned: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, glossary_path: str, banned_path: str) -> "CanonStore":
        store = cls(glossary_path, banned_path)
        if os.path.exists(glossary_path):
            try:
                with open(glossary_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                store.characters = dict(data.get("characters") or {})
                store.facts = list(data.get("facts") or [])
            except (OSError, ValueError):
                pass
        if os.path.exists(banned_path):
            try:
                with open(banned_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                store.banned = list(data.get("motifs") or [])
            except (OSError, ValueError):
                pass
        return store

    def save(self) -> None:
        _atomic_write_json(self.glossary_path,
                           {"version": 1, "characters": self.characters, "facts": self.facts})
        _atomic_write_json(self.banned_path, {"version": 1, "motifs": self.banned})

    def render_block(self, max_chars: int = 1200) -> str:
        """每章注入的"硬设定"块。空 canon 返回空串。"""
        lines: list[str] = []
        if self.characters:
            lines.append("【人工指定角色（必须按此设定出场）】")
            for name, setting in self.characters.items():
                lines.append(f"- {name}：{setting}")
        if self.facts:
            lines.append("【人工锁定事实（任何章节不得违反）】")
            for fact in self.facts:
                lines.append(f"- {fact.get('text', '')}")
        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n- （canon 过长已截断）"
        return text


def _atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------- 读写


def _read_jsonl(path: str) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    if not os.path.exists(path):
        return rows, errors
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                errors.append(f"{path}:{lineno} JSON 解析失败：{e}")
    return rows, errors


def load_applied_ids(applied_path: str) -> set[str]:
    rows, _ = _read_jsonl(applied_path)
    return {str(r.get("id")) for r in rows if r.get("id")}


def load_pending(inbox_path: str, applied_path: str) -> tuple[list[Directive], list[str]]:
    """读未生效的指令。返回 (指令, 错误)；错误要打印出来，不静默丢。"""
    rows, errors = _read_jsonl(inbox_path)
    applied = load_applied_ids(applied_path)
    out: list[Directive] = []
    seen: set[str] = set()
    for row in rows:
        d = Directive.from_dict(row)
        if d.id in applied or d.id in seen:
            continue
        errs = d.validate()
        if errs:
            errors.append(f"指令 {d.id or '<无 id>'} 非法：{'；'.join(errs)}")
            continue
        seen.add(d.id)
        out.append(d)
    return out, errors


def mark_applied(applied_path: str, directives: list[Directive], chapter: int) -> None:
    if not directives:
        return
    os.makedirs(os.path.dirname(applied_path) or ".", exist_ok=True)
    with open(applied_path, "a", encoding="utf-8") as f:
        for d in directives:
            record = {"id": d.id, "type": d.type, "chapter": chapter,
                      "applied_at": datetime.now().isoformat(timespec="seconds")}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 应用


@dataclass
class ApplyResult:
    consumed: list[str] = field(default_factory=list)       # 已生效的指令 id
    notes: list[str] = field(default_factory=list)          # 一次性注入文本
    rewind_chapter: int | None = None
    errors: list[str] = field(default_factory=list)

    def render_notes(self) -> str:
        return "\n".join(n for n in self.notes if n).strip()


def apply_directives(directives: list[Directive], *, chapter: int,
                     canon: CanonStore) -> ApplyResult:
    """把指令落到 canon / 一次性备注 / 回滚请求上。

    幂等性由调用方保证（只对 pending 调本函数，生效后 mark_applied）。
    """
    res = ApplyResult()
    for d in directives:
        if d.type == "insert_character":
            name = str(d.payload.get("name", "")).strip()
            setting = str(d.payload.get("setting", "")).strip()
            canon.characters[name] = setting or "（人工指定角色，设定待补）"
            res.notes.append(f"- 本章起 {name} 已进入 canon，必须按《{canon.characters[name]}》设定出场。")
            res.consumed.append(d.id)
        elif d.type == "lock_fact":
            text = str(d.payload.get("text") or d.payload.get("fact") or "").strip()
            canon.facts.append({"text": text, "chapter": chapter, "source": d.id})
            res.notes.append(f"- 已锁定事实：{text}")
            res.consumed.append(d.id)
        elif d.type == "steer_plot":
            text = str(d.payload.get("text", "")).strip()
            res.notes.append(f"- 一次性剧情纠偏（只作用于本章）：{text}")
            res.consumed.append(d.id)
        elif d.type == "ban_motif":
            motif = str(d.payload.get("motif", "")).strip()
            if motif and motif not in canon.banned:
                canon.banned.append(motif)
            res.notes.append(f"- 禁用桥段「{motif}」已加入黑名单，本章及以后不得再写。")
            res.consumed.append(d.id)
        elif d.type == "rewrite_chapter":
            res.rewind_chapter = int(d.payload["chapter"])
            res.notes.append(f"- 已登记回滚重写：第 {res.rewind_chapter} 章及之后作废重来。")
            res.consumed.append(d.id)
        else:
            res.errors.append(f"未实现的指令类型：{d.type}")
    return res


def enqueue(directive: Directive, inbox_path: str) -> None:
    """往 inbox 追加一条（给 CLI / GUI 用）。"""
    if not directive.created:
        directive.created = datetime.now().isoformat(timespec="seconds")
    errs = directive.validate()
    if errs:
        raise ValueError("；".join(errs))
    os.makedirs(os.path.dirname(inbox_path) or ".", exist_ok=True)
    with open(inbox_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(directive.to_dict(), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="人工干预指令 CLI")
    ap.add_argument("--inbox", default=os.path.join("config", "directives", "inbox.jsonl"))
    ap.add_argument("--applied", default=os.path.join("config", "directives", "applied.jsonl"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="追加一条指令")
    p.add_argument("type", choices=sorted(VALID_TYPES))
    p.add_argument("--id", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--payload", default="{}", help="JSON 字符串")

    sub.add_parser("list", help="列出待生效指令")

    args = ap.parse_args()
    if args.cmd == "add":
        d = Directive(id=args.id, type=args.type, payload=json.loads(args.payload), note=args.note)
        enqueue(d, args.inbox)
        print(f"已投递 {d.id} {d.type}（下一章生效）")
    else:
        pending, errors = load_pending(args.inbox, args.applied)
        for d in pending:
            print(f"[{d.id}] {d.type} {json.dumps(d.payload, ensure_ascii=False)} {d.note}")
        if not pending:
            print("（无待生效指令）")
        for e in errors:
            print("ERROR:", e)
