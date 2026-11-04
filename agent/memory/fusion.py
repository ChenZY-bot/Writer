"""多路召回融合：把 fish 的排序四件套移植到"小说记忆检索"上。

问题
----
现在的检索是单路：用 `chapter_plan` 去 Chroma 做一次 `similarity_search(k=8)`。
三个后果：

1. **一把尺子量所有东西**：细纲文本和正文片段相似度天然偏低，捞回来的常常是同题材
   其他章节，而不是"这章要用的那个设定"。
2. **没有多样性**：8 条里 5 条来自同一章同一场景，另外 3 条是重复段落。
3. **没有时效**：三个月前埋的伏笔和上一章的设定同权，模型于是只写眼前。

fish 的解法（`p0/fish_push_rank.py`）
------------------------------------
* **RRF**（Reciprocal Rank Fusion, Cormack 2009）：每条路只在路内排名，
  `score = Σ 1/(k + rank)`，k=60。名次制，天然免疫"三把尺子量纲不同"。
* **三因子**（Generative Agents）：recency × importance × relevance，做幂加权。
* **MMR**：`λ·rel − (1−λ)·max sim`，防止两条几乎一样的条目占两格。
* **价值门**：低于阈值不推，但给新条目 0.15 的底，避免"把自己关掉"。

这里照搬结构，但把"importance"换成小说里真正有信号的量：
**该片段被引用过的次数**（由检索后回写，或人工加权），没有就退化为 1.0。

纯 stdlib，不依赖 langchain/chroma —— 便于单测，也便于换向量库。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")


@dataclass
class Candidate:
    id: str
    text: str
    route: str                     # vector / entity / thread / recent ...
    score: float = 0.0             # 路内原始分（越高越相关）
    chapter: int | None = None
    importance: float = 1.0        # 被引用次数等外部信号，缺省中性
    meta: dict = field(default_factory=dict)

    def with_score(self, score: float) -> "Candidate":
        return Candidate(self.id, self.text, self.route, score,
                         self.chapter, self.importance, dict(self.meta))


def _norm(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", text or "")


def _grams(text: str, n: int = 3) -> set[str]:
    s = _norm(text)
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def similarity(a: str, b: str) -> float:
    """字符 3-gram Jaccard：中文段落相似度的廉价代理，不需要 embedding。"""
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


# ---------------------------------------------------------------- 去重


def _is_duplicate(a: str, b: str, threshold: float) -> bool:
    """包含关系也算重复：切块带来的"整段与整段+一句"是 3-gram 的盲区。"""
    na, nb = _norm(a), _norm(b)
    if len(na) >= 20 and len(nb) >= 20:
        if na in nb or nb in na:
            return True
    return similarity(a, b) >= threshold


def dedupe(cands: list[Candidate], threshold: float = 0.80) -> list[Candidate]:
    """近重复合并：保留路内分数更高、正文更长的一条。

    Chroma 按 500 字切块、重叠 50 字，相邻块天然重复 10%；
    再加上同一事件被写过多次，8 条召回里常常只有 4 条是新信息。
    """
    kept: list[Candidate] = []
    # 先按正文长度降序：包含关系的重复里，永远留信息更全的那条
    for cand in sorted(cands, key=lambda c: (-len(c.text), -c.score)):
        replaced = False
        for idx, k in enumerate(kept):
            if _is_duplicate(cand.text, k.text, threshold):
                if len(cand.text) > len(k.text) or (
                        len(cand.text) == len(k.text) and cand.score > k.score):
                    kept[idx] = cand
                replaced = True
                break
        if not replaced:
            kept.append(cand)
    kept.sort(key=lambda c: -c.score)
    return kept


# ---------------------------------------------------------------- RRF


def rrf_fuse(routes: dict[str, list[Candidate]], k: int = 60) -> list[Candidate]:
    """跨路融合。只改名次，不改路内分数。

    `routes` 形如 {"vector": [...], "entity": [...], "thread": [...]}。
    每个 Candidate 的 id 是跨路去重的唯一键。
    """
    fused: dict[str, float] = {}
    pool: dict[str, Candidate] = {}
    for _route, items in routes.items():
        ranked = sorted(items, key=lambda c: (-c.score, c.id))
        for rank, cand in enumerate(ranked, start=1):
            fused[cand.id] = fused.get(cand.id, 0.0) + 1.0 / (k + rank)
            # 同一 id 在不同路重复出现时，正文取更长的那个（信息更全）
            if cand.id not in pool or len(cand.text) > len(pool[cand.id].text):
                pool[cand.id] = cand

    if not fused:
        return []
    top = max(fused.values()) or 1.0
    out = [pool[cid].with_score(round(score / top, 6)) for cid, score in fused.items()]
    out.sort(key=lambda c: -c.score)
    return out


# ---------------------------------------------------------------- 三因子


def apply_three_factor(cands: list[Candidate], current_chapter: int, *,
                       w_recency: float = 0.2,
                       w_importance: float = 0.3,
                       w_relevance: float = 0.5,
                       half_life: float = 20.0) -> list[Candidate]:
    """recency × importance × relevance 的幂加权（Generative Agents 版）。

    * recency：`0.5 ** (age / half_life)`，半衰期默认 20 章。
    * importance：`log1p(n) / log1p(50)`，50 次封顶；没有信号时为 1.0（中性，不误杀）。
    * relevance：融合后的 score。
    全部取 max(x, 0.01) 再乘，避免任何一项为 0 时把候选一刀切死
    —— 这是 fish 在 `_value_ok` 里踩过的真实坑。
    """
    out: list[Candidate] = []
    for c in cands:
        age = max(0, current_chapter - (c.chapter if c.chapter is not None else current_chapter))
        recency = 0.5 ** (age / half_life) if half_life > 0 else 1.0
        imp = 1.0 if c.importance <= 0 else min(1.0, math.log1p(c.importance) / math.log1p(50.0))
        rel = max(0.0, min(c.score, 1.0))
        score = (max(rel, 0.01) ** w_relevance
                 * max(imp, 0.01) ** w_importance
                 * max(recency, 0.01) ** w_recency)
        out.append(c.with_score(round(min(score, 1.0), 6)))
    out.sort(key=lambda c: -c.score)
    return out


# ---------------------------------------------------------------- MMR


def mmr_select(cands: list[Candidate], quota: int, *, lam: float = 0.7) -> list[Candidate]:
    """最大边际相关：既相关又彼此不同。

    fish 的相似度是"同 kind+项目 0.4 / 标题字集 Jaccard"，这里直接用正文 3-gram
    Jaccard —— 小说片段没有 kind/title，正文本身就是相似度来源。
    """
    if quota <= 0:
        return []
    pool = list(cands)
    chosen: list[Candidate] = []
    while pool and len(chosen) < quota:
        best, best_val = None, None
        for cand in pool:
            div = max((similarity(cand.text, s.text) for s in chosen), default=0.0)
            val = lam * cand.score - (1.0 - lam) * div
            if best_val is None or val > best_val:
                best, best_val = cand, val
        assert best is not None
        chosen.append(best)
        pool.remove(best)
    return chosen


# ---------------------------------------------------------------- 编排


def select(routes: dict[str, list[Candidate]], *, quota: int = 8,
           current_chapter: int = 1, use_factors: bool = False,
           use_mmr: bool = True, dedupe_threshold: float = 0.80,
           lam: float = 0.7) -> list[Candidate]:
    """完整流水线：RRF 融合 -> 去重 -> （可选）三因子 -> （可选）MMR -> 截断。

    默认 `use_factors=False`：三因子会改变排序，按参考库的纪律默认关，
    观察一轮再开。
    """
    fused = rrf_fuse(routes)
    if not fused:
        return []
    fused = dedupe(fused, threshold=dedupe_threshold)
    if use_factors:
        fused = apply_three_factor(fused, current_chapter)
    if use_mmr and quota > 1:
        return mmr_select(fused, quota, lam=lam)
    return fused[:quota]
