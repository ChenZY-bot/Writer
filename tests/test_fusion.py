"""多路召回融合的判据（RRF / 三因子 / MMR / 去重）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.memory.fusion import (
    Candidate, apply_three_factor, dedupe, mmr_select, rrf_fuse, select, similarity,
)


def cand(cid, text, route="vector", score=1.0, chapter=None):
    return Candidate(id=cid, text=text, route=route, score=score, chapter=chapter)


class TestRRF(unittest.TestCase):
    def test_candidate_in_two_routes_wins(self):
        routes = {
            "vector": [cand("a", "铜镜的裂痕", score=0.9), cand("b", "玉佩的来历", score=0.8)],
            "entity": [cand("b", "玉佩的来历", route="entity", score=0.6)],
        }
        fused = rrf_fuse(routes)
        self.assertEqual(fused[0].id, "b", "两路都命中的条目应排第一")
        self.assertGreater(fused[0].score, fused[1].score)

    def test_scores_normalized_to_one(self):
        fused = rrf_fuse({"vector": [cand("a", "x", score=1), cand("b", "y", score=0.5)]})
        self.assertAlmostEqual(fused[0].score, 1.0, places=6)

    def test_empty_routes(self):
        self.assertEqual(rrf_fuse({}), [])

    def test_rank_order_within_route(self):
        fused = rrf_fuse({"vector": [cand("a", "高", score=0.9), cand("b", "低", score=0.1)]})
        self.assertEqual([c.id for c in fused], ["a", "b"])


class TestDedupe(unittest.TestCase):
    def test_near_duplicates_merged(self):
        text = "主角走进便利店，拿起一盒牛奶，看了看保质期又放下。"
        rows = [cand("a", text, score=0.9), cand("b", text + "。", score=0.8)]
        self.assertEqual(len(dedupe(rows)), 1)

    def test_keeps_higher_score_and_longer_text(self):
        text = "主角走进便利店，拿起一盒牛奶，看了看保质期又放下。"
        rows = [cand("short", text, score=0.9), cand("long", text + "他转身离开。", score=0.8)]
        kept = dedupe(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].id, "long")

    def test_distinct_texts_kept(self):
        rows = [cand("a", "铜镜认主，剑身裂开一道细缝。"), cand("b", "玉佩在灯下泛出冷光。")]
        self.assertEqual(len(dedupe(rows)), 2)

    def test_similarity_is_symmetric(self):
        a, b = "主角走进便利店", "主角走进便利店买牛奶"
        self.assertAlmostEqual(similarity(a, b), similarity(b, a))


class TestThreeFactor(unittest.TestCase):
    def test_recency_prefers_newer(self):
        rows = [cand("old", "旧章节内容", chapter=1, score=0.8),
                cand("new", "新章节内容", chapter=87, score=0.8)]
        out = apply_three_factor(rows, current_chapter=88)
        self.assertEqual(out[0].id, "new")

    def test_zero_importance_does_not_kill_candidate(self):
        # fish 踩过的坑：importance=0 会把候选一刀切死
        rows = [Candidate("a", "内容", "vector", 0.9, chapter=88, importance=0.0)]
        out = apply_three_factor(rows, current_chapter=88)
        self.assertGreater(out[0].score, 0.0)

    def test_only_rescores_no_drop(self):
        rows = [cand(f"c{i}", f"内容{i}", chapter=i) for i in range(5)]
        self.assertEqual(len(apply_three_factor(rows, 10)), 5)


class TestMMR(unittest.TestCase):
    def test_prefers_diversity(self):
        rows = [
            cand("a", "学园祭的准备，彩排，舞台，灯光，摊位", score=1.0),
            cand("b", "学园祭的准备，彩排，舞台，灯光，横幅", score=0.99),   # 与 a 近重复
            cand("c", "山间神社的巫女在雨里擦拭一枚玉佩", score=0.9),        # 完全不同
        ]
        chosen = mmr_select(rows, 2, lam=0.7)
        ids = [c.id for c in chosen]
        self.assertEqual(ids[0], "a")
        self.assertEqual(ids[1], "c", "MMR 应该选不同的那条，而不是相似度第二高的")

    def test_quota_zero(self):
        self.assertEqual(mmr_select([cand("a", "x")], 0), [])

    def test_quota_larger_than_pool(self):
        self.assertEqual(len(mmr_select([cand("a", "x"), cand("b", "y")], 5)), 2)


class TestPipeline(unittest.TestCase):
    def test_select_respects_quota_and_dedupes(self):
        texts = [
            "铜镜认主，剑身裂开一道细缝，血珠渗进去",
            "玉佩在灯下泛出冷光，巫女伸手去接",
            "便利店的自动门开了一半又合上",
            "雨把操场泡成一片泥，球鞋踩下去陷进半寸",
            "山间神社的铃声响了三下，停住",
        ]
        routes = {"vector": [cand(f"v{i}", t) for i, t in enumerate(texts)]}
        out = select(routes, quota=3, current_chapter=10)
        self.assertEqual(len(out), 3)
        self.assertEqual(len({c.id for c in out}), 3)

    def test_select_empty(self):
        self.assertEqual(select({}, quota=3), [])

    def test_select_without_mmr_takes_top(self):
        routes = {"vector": [cand("a", "内容甲", score=0.9), cand("b", "内容乙", score=0.8)]}
        out = select(routes, quota=1, use_mmr=False)
        self.assertEqual(out[0].id, "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
