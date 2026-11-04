"""客观质量闸：把"能机器判的"从 LLM 审稿里摘出来。

背景
----
现在的审稿人是唯一的关卡，而它是纯主观 LLM 判断：全量日志 207 次审查只打回 10 次。
更糟的是它的核心判据（"只有重演【已完结】事件才打回"）因为事件账本不收敛而永不触发。

这个模块只做**能给出证据的判断**，不碰"文笔好不好"这类主观项：

  G-WORDS        字数偏离目标（默认 2500，允许 0.8~1.3 倍）
  G-TITLE        标题格式（重复章节号 / 缺标题）
  G-ANCHOR       与上一章结尾的物理衔接（字面锚点缺失，仅提示）
  G-WHITELIST    细纲白名单外的人物出场（需要提供花名册，否则跳过）
  G-MOTIF-BANNED 禁写桥段
  G-REPEAT-*     复读三判据（复用 repetition 模块）
  G-META         场外元文本（本章完 / 下章预告 / 字数统计）

设计原则（照抄两个参考库的纪律）
------------------------------
1. **判据必须能失败**：每条 hard 判据都有测试用例，改坏实现测试会红。
2. **不静默降级**：花名册没提供就不检查，并在报告里标 `skipped`，不假装通过。
3. **能自动修的不打回**：格式类问题走 `normalize_chapter` 直接修，只有内容问题才要求重写。

纯 stdlib。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.quality.issue import Issue
from agent.quality.repetition import RepeatConfig, analyze

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")
_META = re.compile(
    r"(\(本章完\)|（本章完）|本章完|未完待续|下章预告|作者题外话|"
    r"（全文\s*\d+\s*字）|\(全文\s*\d+\s*字\)|全文\s*\d+\s*字)"
)
_DUP_CHAPTER_IN_TITLE = re.compile(r"^#+\s*第\s*\d+\s*章[\s　:：、.-]*")
_HEADING = re.compile(r"^#+\s*(.+)$")

# 上一章结尾是否"话没说完"（没有终止标点）
_TERMINAL = re.compile(r"[。！？…”」』】~～\.!?]$")
# 强行转场：prompt 明令禁止开篇使用的词
_TRANSITION = re.compile(
    r"^[#\s　]*[（(]?(第[二三四五六七八九十百\d]+天|次日|翌日|几天后|数日后|"
    r"一周后|半月后|一个月后|数月后|多年后|与此同时|转眼间?|不久后)"
)


def count_words(text: str) -> int:
    """中文按字、英文数字按词，接近网文平台的"字数"口径。"""
    return len(_CJK.findall(text)) + len(_ASCII_WORD.findall(text))


@dataclass
class NormalizeResult:
    text: str
    fixes: list[str] = field(default_factory=list)


def normalize_chapter(text: str, chapter_num: int) -> NormalizeResult:
    """清理格式，不改内容。

    修复项（都是第 1~88 章里真实出现过的）：
      * 正文里的"（本章完）/下章预告/全文字数"等场外元文本
      * 标题重复章节号：`# 第40章 捞金鱼赌约` -> `# 捞金鱼赌约`（文件头已经写了"第 N 章"）
      * 三个以上连续空行
    """
    fixes: list[str] = []
    body = text or ""

    cleaned = _META.sub("", body)
    if cleaned != body:
        fixes.append("移除场外元文本")
        body = cleaned

    lines = body.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.lstrip().startswith("#") and _DUP_CHAPTER_IN_TITLE.match(line):
            lines[i] = _DUP_CHAPTER_IN_TITLE.sub("# ", line).rstrip()
            fixes.append("移除标题内重复的章节号")
        break
    body = "\n".join(lines)

    squeezed = re.sub(r"\n{3,}", "\n\n", body).strip()
    if squeezed != body.strip():
        fixes.append("合并多余空行")
    body = squeezed

    if body and not body.lstrip().startswith("#"):
        # 没有标题不算错误，但提示一下：文件头会另写"第 N 章"，正文标题内不要再重复章节号
        fixes.append("缺少章节标题（未自动补）")

    return NormalizeResult(text=body, fixes=fixes)


def _first_body_line(body: str) -> str:
    """跳过标题行，取正文第一行。"""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    if lines and _HEADING.match(lines[0]):
        return lines[1] if len(lines) > 1 else ""
    return lines[0] if lines else ""


def check_chapter(
    text: str,
    *,
    chapter_num: int,
    target_words: int = 2500,
    min_ratio: float = 0.72,
    max_ratio: float = 1.30,
    anchor_tail: str = "",
    recent_texts: list[str] | None = None,
    banned_motifs: list[str] | None = None,
    allowed_characters: list[str] | None = None,
    roster: list[str] | None = None,
    cfg: RepeatConfig | None = None,
) -> tuple[list[Issue], list[str]]:
    """返回 (issues, skipped_checks)。

    `skipped_checks` 显式列出"因为缺数据没做的检查"，避免"看起来全绿"的假象。
    """
    issues: list[Issue] = []
    skipped: list[str] = []
    body = text or ""

    # ---- 元文本 ----
    meta = _META.findall(body)
    if meta:
        issues.append(Issue("G-META", "hard",
                            f"正文里出现场外元文本：{'、'.join(sorted(set(m[0] for m in meta)))}。只允许纯正文。"))

    # ---- 字数 ----
    words = count_words(body)
    low, high = int(target_words * min_ratio), int(target_words * max_ratio)
    if words < low or words > high:
        issues.append(Issue("G-WORDS", "hard",
                            f"本章 {words} 字，超出允许区间 {low}~{high}（目标 {target_words}）。"
                            "请把篇幅调整到区间内，不要靠排比短句凑字数。"))

    # ---- 标题 ----
    first = next((ln for ln in body.splitlines() if ln.strip()), "")
    if not _HEADING.match(first):
        issues.append(Issue("G-TITLE", "warn", "正文首行不是章节标题（应形如 `# 标题`），"
                                                  "文件头会另写“第 N 章”，正文标题内不要再重复章节号。"))
    elif _DUP_CHAPTER_IN_TITLE.match(first):
        issues.append(Issue("G-TITLE", "warn", "标题内重复了章节号（`# 第N章 ...`），"
                                                  "文件头已有序号，标题只留标题本身。"))

    # ---- 物理衔接 ----
    # 两种情况分开判（用真实语料校准过：无条件比对末十字会在 93% 的正常章节上误报）：
    #   1. 上一章结尾"话没说完"（无终止标点）-> 必须在本章开头接上；
    #   2. 无论锚点如何，开篇都不许用"第二天/与此同时"这类强行转场词。
    anchor = (anchor_tail or "").lstrip(".").strip()
    first_line = _first_body_line(body)
    if anchor and not _TERMINAL.search(anchor):
        probe = re.sub(r"[\s\u3000]+", "", anchor)[-10:]
        head = re.sub(r"[\s\u3000]+", "", body[:400])
        if probe and probe not in head:
            issues.append(Issue(
                "G-ANCHOR", "warn",
                f"上一章结尾是一句没说完的话（「{anchor[-12:]}」），本章开头没有接上它。",
                evidence=anchor[-30:],
            ))
    elif not anchor:
        skipped.append("G-ANCHOR（未提供上一章锚点）")

    m = _TRANSITION.match(first_line[:40]) if first_line else None
    if m:
        issues.append(Issue(
            "G-TRANSITION", "warn",
            f"开篇使用了强行转场词「{m.group(1)}」。上一章结尾到这里必须是连贯的一页纸。",
        ))

    # ---- 人物白名单 ----
    if roster and allowed_characters is not None:
        allowed = set(allowed_characters)
        intruders = [n for n in roster if n and n in body and n not in allowed]
        if intruders:
            issues.append(Issue("G-WHITELIST", "hard",
                                f"细纲未准许出场的人物出现了：{'、'.join(intruders[:6])}。"
                                "要么删掉，要么在细纲里补授权。"))
    else:
        skipped.append("G-WHITELIST（未提供花名册/白名单）")

    # ---- 复读 / 禁写桥段 ----
    report = analyze(body, recent_texts=recent_texts,
                     banned_motifs=banned_motifs, cfg=cfg)
    issues.extend(report.issues)

    return issues, skipped


def feedback_from(issues: list[Issue], max_items: int = 6) -> str:
    """把 hard issue 拼成给写手的重写指令；没有 hard issue 返回空串。"""
    hard = [i for i in issues if i.hard]
    if not hard:
        return ""
    head = f"客观质量闸未通过（{len(hard)} 项硬性问题），必须逐条修正后重写："
    return "\n".join([head] + [f"{n}. {i.render()}" for n, i in enumerate(hard[:max_items], 1)])


def gate_update(issues: list[Issue], *, mode: str, revision_count: int) -> dict:
    """纯决策：把"判据结果"翻译成"要写回 state 的字段"。

    单独抽出来是为了能测——这里藏过一个会让流水线卡死的 bug：
    放行时如果不把 `feedback` 清空，上一轮打回留下的反馈会一直挂在 state 上，
    路由每一轮都判 failed，直到 max_retries 才停。
    所以**任何放行分支都必须显式返回 `feedback: ""`**。
    """
    mode = (mode or "off").lower()
    hard = [i for i in issues if i.hard]
    if mode not in ("dry", "enforce") or mode == "dry" or not hard:
        return {"feedback": ""}
    return {
        "feedback": feedback_from(issues),
        "revision_count": int(revision_count) + 1,
    }
