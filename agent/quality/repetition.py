"""复读检测：把 dsh-degeneration-kit 的两把尺子移植到小说正文上。

来源与依据
----------
`thinking-guard/lib/detect-v2.mjs` 把"复读"拆成两型，并给出实测分界：

  A 型 · 同一句死磕：单个单元占尾部窗口 ≥ 0.20，且重复次数 ≥ 8。
  B 型 · 短句轮换：极短单元占比 ≥ 0.35 且 Top5 单元合计占比 ≥ 0.50（样本 ≥ 25 段）。
        只卡 A 型会整块放行——原文实测"英文为主的 8 个块全被这道门拦掉"。

为什么小说也会得这个病
----------------------
写手模型在长上下文里会退化成"安全句式轮换"：

    她笑了。他也笑了。两人都笑了。她看着他。他看着她。……

或者更隐蔽的"桥段复读"：每章都写"修罗场抢着塞东西"“一起吃饭和解”。
前者是句级，后者是章级。所以这里有三层判据：

  1. 句级 A 型（死磕）
  2. 句级 B 型（短句轮换）
  3. 章级：与最近 K 章的 8-gram 重合度（桥段/段落级抄自己）

与原实现的差异（如实标注，不假装等价）
--------------------------------------
* 原实现按"英文词数 ≤3"定义短单元；中文按字符数，这里取 `weight ≤ 8`（汉字计 1，英文单词计 1）。
* 原实现是"思考块"窗口 4000 字符；小说正文按整章分析，另对尾部 4000 字符再跑一次（结尾最容易灌水）。
* 阈值全部可配，默认值落在原库给出的实测分界上。

纯 stdlib，无外部依赖。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.quality.issue import Issue

_UNIT_SPLIT = re.compile(r"[。！？!?；;\n]+|\.\s+")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")
_CODE_FENCE = re.compile(r"```[\s\S]*?```")


def unit_weight(text: str) -> int:
    """单元长度：汉字 1 字算 1，英文/数字按词算 1（与 dsh 的 norm_words 同思路）。"""
    return len(_CJK.findall(text)) + len(_ASCII_WORD.findall(text))


def split_units(text: str) -> list[str]:
    """切成句子单元。

    必须丢掉**纯标点单元**：中文对白写成 `“……。”` 时，按句号切开会留下一个
    `”`。真实语料校准发现：第 3/5/8 章里 `”` 分别出现 16/18/15 次，占全部句子
    的 20% 以上，会让 A 型判据直接把正常章节判成复读（88 章里误报 20 章）。
    """
    text = _CODE_FENCE.sub(" ", text or "")
    units: list[str] = []
    for part in _UNIT_SPLIT.split(text):
        part = part.strip()
        if part and unit_weight(part) > 0:
            units.append(part)
    return units


def _tail(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[-chars:]


@dataclass
class RepeatConfig:
    # A 型
    a_min_share: float = 0.20
    a_min_count: int = 8
    # B 型（原库实测分界：0.35 / 0.50，样本 >= 25）
    short_weight_max: int = 8
    b_short_share_min: float = 0.35
    b_top_unit_share_min: float = 0.50
    b_min_parts: int = 25
    # 尾部复检窗口
    tail_chars: int = 4000
    # 章级 8-gram 重合
    ngram: int = 8
    cross_warn: float = 0.06
    cross_hard: float = 0.12


def detect_degenerate(text: str, cfg: RepeatConfig | None = None,
                      scope: str = "本章") -> list[Issue]:
    """句级 A/B 双判据。返回 hard issue 列表（空表示干净）。"""
    cfg = cfg or RepeatConfig()
    units = split_units(text)
    if len(units) < cfg.b_min_parts:
        return []

    counts: dict[str, int] = {}
    short = 0
    for u in units:
        if unit_weight(u) <= cfg.short_weight_max:
            short += 1
        counts[u] = counts.get(u, 0) + 1

    total = len(units)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    top_unit, top_count = ranked[0]
    top5 = sum(n for _, n in ranked[:5])
    top_share = top5 / total
    short_share = short / total

    issues: list[Issue] = []

    # A 型：同一句死磕
    if top_count >= cfg.a_min_count and (top_count / total) >= cfg.a_min_share:
        issues.append(Issue(
            code="G-REPEAT-A", severity="hard",
            message=(f"{scope}出现「同一句死磕」：句子“{top_unit[:30]}”重复 {top_count} 次，"
                     f"占全部 {total} 句的 {top_count / total:.0%}。必须改写为不同表达。"),
            evidence=top_unit[:40],
        ))

    # B 型：短句轮换（A 型没命中时才看，避免同一现象报两条）
    if not issues and short_share >= cfg.b_short_share_min and top_share >= cfg.b_top_unit_share_min:
        detail = "、".join(f"“{u[:12]}”×{n}" for u, n in ranked[:5])
        issues.append(Issue(
            code="G-REPEAT-B", severity="hard",
            message=(f"{scope}出现「短句轮换」：{total} 句中有 {short} 句是极短句（{short_share:.0%}），"
                     f"Top5 句式合计占 {top_share:.0%}（{detail}）。"
                     "这是最隐蔽的复读形态，请打散句式长度、换成实写内容。"),
            evidence=detail[:60],
        ))

    # 尾部复检：结尾最容易灌水，单独再看一次 4000 字窗口
    tail_units = split_units(_tail(text, cfg.tail_chars))
    if len(tail_units) >= cfg.b_min_parts and not issues:
        t_counts: dict[str, int] = {}
        t_short = 0
        for u in tail_units:
            if unit_weight(u) <= cfg.short_weight_max:
                t_short += 1
            t_counts[u] = t_counts.get(u, 0) + 1
        t_total = len(tail_units)
        t_top5 = sum(n for _, n in sorted(t_counts.items(), key=lambda kv: -kv[1])[:5])
        if (t_short / t_total) >= cfg.b_short_share_min and (t_top5 / t_total) >= cfg.b_top_unit_share_min:
            issues.append(Issue(
                code="G-REPEAT-TAIL", severity="hard",
                message=f"{scope}结尾 4000 字内出现短句轮换（极短句 {t_short / t_total:.0%}，"
                        f"Top5 占 {t_top5 / t_total:.0%}），结尾不允许用排比短句凑字数。",
            ))

    return issues


def _ngrams(text: str, n: int) -> set[str]:
    s = re.sub(r"[\s\u3000]+", "", text or "")
    s = re.sub(r"^#.*$", "", s, flags=re.MULTILINE)
    if len(s) < n:
        return set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def cross_chapter_overlap(text: str, recent_texts: list[str],
                          cfg: RepeatConfig | None = None) -> list[Issue]:
    """章级：与最近 K 章的 8-gram 重合度。

    这里不用 embedding：字面 8-gram 对"整段改写过的旧桥段"不敏感，但对
    "原样搬运自己的句子"极其敏感——而那正是自动流水线最常见的偷懒方式。
    """
    cfg = cfg or RepeatConfig()
    cur = _ngrams(text, cfg.ngram)
    if not cur:
        return []
    issues: list[Issue] = []
    for idx, recent in enumerate(recent_texts):
        old = _ngrams(recent, cfg.ngram)
        if not old:
            continue
        jac = len(cur & old) / len(cur | old)
        if jac >= cfg.cross_hard:
            issues.append(Issue(
                code="G-CROSS-DUP", severity="hard",
                message=(f"本章与最近第 {idx + 1} 章（倒序）字面重合度 {jac:.1%}，"
                         "超过 12% 硬阈值，属于搬运旧章节。必须换场景或换冲突。"),
            ))
            break
        if jac >= cfg.cross_warn:
            issues.append(Issue(
                code="G-CROSS-WARN", severity="warn",
                message=f"本章与最近第 {idx + 1} 章字面重合度 {jac:.1%}，接近上限，注意别复写同一个桥段。",
            ))
            break
    return issues


def banned_motif_hits(text: str, banned: list[str]) -> list[Issue]:
    """禁写桥段（人工设定 + 已用过的场景名）命中检查。"""
    issues: list[Issue] = []
    for motif in banned:
        motif = (motif or "").strip()
        if len(motif) < 2:
            continue
        if motif in text:
            issues.append(Issue(
                code="G-MOTIF-BANNED", severity="hard",
                message=f"本章出现了被禁止复用的桥段/场景「{motif}」。请换一个全新的事件设计。",
                evidence=motif,
            ))
    return issues


@dataclass
class RepetitionReport:
    issues: list[Issue] = field(default_factory=list)
    sentences: int = 0
    short_share: float = 0.0
    top5_share: float = 0.0

    @property
    def clean(self) -> bool:
        return not any(i.hard for i in self.issues)


def analyze(text: str, *, recent_texts: list[str] | None = None,
            banned_motifs: list[str] | None = None,
            cfg: RepeatConfig | None = None) -> RepetitionReport:
    """一站式：句级 + 章级 + 禁写桥段。"""
    cfg = cfg or RepeatConfig()
    units = split_units(text)
    counts: dict[str, int] = {}
    short = 0
    for u in units:
        counts[u] = counts.get(u, 0) + 1
        if unit_weight(u) <= cfg.short_weight_max:
            short += 1
    total = max(1, len(units))
    top5 = sum(n for _, n in sorted(counts.items(), key=lambda kv: -kv[1])[:5])

    issues = detect_degenerate(text, cfg)
    if recent_texts:
        issues += cross_chapter_overlap(text, recent_texts, cfg)
    if banned_motifs:
        issues += banned_motif_hits(text, banned_motifs)

    return RepetitionReport(issues=issues, sentences=len(units),
                            short_share=short / total, top5_share=top5 / total)
