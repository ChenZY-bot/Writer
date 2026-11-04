"""复读三判据与客观质量闸的判据。

对应参考实现 `thinking-guard/lib/detect-v2.mjs` 的实测分界：
A 型 0.20/8；B 型 0.35/0.50、样本 >= 25。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.quality.gate import (
    check_chapter, count_words, feedback_from, gate_update, normalize_chapter,
)
from agent.quality.issue import Issue
from agent.quality.repetition import (
    RepeatConfig, analyze, banned_motif_hits, cross_chapter_overlap, detect_degenerate,
)

CLEAN_SENTENCES = [
    "窗外的雨敲在铁皮雨棚上，声音像有人用指节一下一下地叩门",
    "主角把塑料袋换到左手，右手伸进兜里摸那枚冰凉的铜钥匙",
    "角色B没有回头，只把伞往他那边偏了半寸，鞋尖溅起一小片水花",
    "楼道里的感应灯坏了三个月，物业的告示被雨水泡成了一团蓝墨",
    "便利店的门铃响了一声，收银台后面的姑娘正在给关东煮换汤",
    "货架最上层堆着打折的烟花，包装纸上印着去年夏天的庙会图案",
    "他拿起一盒牛奶看保质期，又放下了，转而去够最里面的那排罐头",
    "雨声忽然大起来，把自动门开合的提示音压得只剩一点尾音",
    "四个人在门口挤成一团，谁都不肯先进去，最后还是主角让开了半步",
    "角色A的头发上沾着水珠，她抬手捋了一下，动作快得像在掩饰什么",
]


def long_clean(n=40):
    return "。".join(CLEAN_SENTENCES[i % len(CLEAN_SENTENCES)] + f"（第{i}次）" for i in range(n)) + "。"


class TestRepetition(unittest.TestCase):
    def test_a_type_same_sentence_thrash(self):
        units = ["她笑了"] * 12
        units += [s for s in CLEAN_SENTENCES] * 3
        text = "。".join(units) + "。"
        issues = detect_degenerate(text)
        codes = [i.code for i in issues]
        self.assertIn("G-REPEAT-A", codes)
        self.assertTrue(all(i.hard for i in issues))

    def test_b_type_short_rotation_not_caught_by_a(self):
        variants = ["她笑了", "他也笑了", "她看着他", "他看着她", "空气安静了", "谁也没说话"]
        units = variants * 8          # 48 段，每段占比 16.7% < 0.20，A 型抓不到
        text = "。".join(units) + "。"
        report = analyze(text)
        self.assertGreaterEqual(report.sentences, 25)
        self.assertGreaterEqual(report.short_share, 0.35)
        self.assertGreaterEqual(report.top5_share, 0.50)
        codes = [i.code for i in report.issues]
        self.assertIn("G-REPEAT-B", codes)
        self.assertNotIn("G-REPEAT-A", codes)

    def test_b_type_requires_both_conditions(self):
        # 只有短句、没有轮换（每句都不同）-> 不该报 B
        units = [f"他说第{i}个词" for i in range(40)]
        text = "。".join(units) + "。"
        report = analyze(text)
        self.assertLess(report.top5_share, 0.50)
        self.assertFalse([i for i in report.issues if i.code == "G-REPEAT-B"])

    def test_clean_prose_has_no_issues(self):
        report = analyze(long_clean())
        self.assertEqual(report.issues, [], [i.message for i in report.issues])

    def test_short_text_is_not_judged(self):
        # 样本不足 25 段时宁可不判，也不能误杀
        self.assertEqual(detect_degenerate("她笑了。她笑了。她笑了。"), [])

    def test_cross_chapter_duplicate_is_hard(self):
        base = "。".join(CLEAN_SENTENCES * 3)
        issues = cross_chapter_overlap(base, [base])
        self.assertTrue(any(i.code == "G-CROSS-DUP" for i in issues))

    def test_cross_chapter_different_text_is_clean(self):
        issues = cross_chapter_overlap(long_clean(), ["完全不同的另一章内容" * 50])
        self.assertEqual(issues, [])

    def test_banned_motif(self):
        issues = banned_motif_hits("这一章写的是学园祭的准备工作。", ["学园祭"])
        self.assertTrue(issues and issues[0].hard)

    def test_banned_motif_skips_too_short(self):
        self.assertEqual(banned_motif_hits("学园祭", ["学"]), [])

    def test_config_thresholds_are_effective(self):
        # 8/48 = 16.7%：默认 0.20 不触发，放宽到 0.15 必须触发
        units = ["她笑了"] * 8 + [s for s in CLEAN_SENTENCES] * 4
        text = "。".join(units) + "。"
        self.assertEqual(detect_degenerate(text, RepeatConfig(a_min_share=0.30)), [])
        loose_codes = [i.code for i in detect_degenerate(text, RepeatConfig(a_min_share=0.15))]
        self.assertIn("G-REPEAT-A", loose_codes)


class TestGate(unittest.TestCase):
    def test_count_words(self):
        self.assertEqual(count_words("你好世界"), 4)
        self.assertEqual(count_words("hello 世界"), 3)

    def test_word_count_out_of_range(self):
        issues, _ = check_chapter("短文。", chapter_num=1, target_words=2500)
        self.assertTrue(any(i.code == "G-WORDS" and i.hard for i in issues))

    def test_word_count_in_range(self):
        body = "# 标题\n" + "字" * 2400
        issues, _ = check_chapter(body, chapter_num=1, target_words=2500)
        self.assertFalse([i for i in issues if i.code == "G-WORDS"])

    def test_meta_text_is_hard(self):
        body = "正文。\n（本章完）"
        issues, _ = check_chapter(body, chapter_num=1, target_words=2500,
                                  min_ratio=0, max_ratio=99)
        self.assertTrue(any(i.code == "G-META" and i.hard for i in issues))

    def test_duplicate_chapter_number_in_title(self):
        issues, _ = check_chapter("# 第40章 捞金鱼赌约\n" + "字" * 2400,
                                  chapter_num=40, target_words=2500)
        self.assertTrue(any(i.code == "G-TITLE" and not i.hard for i in issues))

    def test_anchor_missing_is_warning_only(self):
        issues, _ = check_chapter("# 标题\n" + "字" * 2400, chapter_num=2,
                                  target_words=2500, anchor_tail="他推开门走了进去")
        self.assertTrue(any(i.code == "G-ANCHOR" and not i.hard for i in issues))

    def test_anchor_present_passes(self):
        issues, _ = check_chapter("# 标题\n他推开门走了进去，屋里没人。" + "字" * 2400,
                                  chapter_num=2, target_words=2500,
                                  anchor_tail="...他推开门走了进去")
        self.assertFalse([i for i in issues if i.code == "G-ANCHOR"])

    def test_whitelist_violation(self):
        issues, _ = check_chapter("# 标题\n反派站在门口，主角抬头。" + "字" * 2400,
                                  chapter_num=3, target_words=2500,
                                  allowed_characters=["主角"], roster=["主角", "反派"])
        self.assertTrue(any(i.code == "G-WHITELIST" and i.hard for i in issues))

    def test_missing_roster_is_reported_as_skipped(self):
        _, skipped = check_chapter("# 标题\n" + "字" * 2400, chapter_num=3, target_words=2500)
        self.assertTrue(any("WHITELIST" in s for s in skipped))
        self.assertTrue(any("ANCHOR" in s for s in skipped))

    def test_quote_only_units_do_not_count(self):
        # 真语料校准抓到的 bug：中文对白按句号切会留下单独的 `”`，
        # 它一度占满 20% 让 A 型判据把 20/88 章误判成复读。
        lines = "".join(f"“第{i}句话说完了。”" for i in range(20))
        text = lines + "。".join(CLEAN_SENTENCES) + "。"
        report = analyze(text)
        self.assertFalse([i for i in report.issues if i.code.startswith("G-REPEAT")],
                         [i.message for i in report.issues])

    def test_anchor_ending_with_period_is_not_checked(self):
        # 上一章正常收尾（有句号）时不做字面比对：93% 的正常章节会被误报
        issues, _ = check_chapter("# 标题\n全新的一天开始了。" + "字" * 2400,
                                  chapter_num=2, target_words=2500,
                                  anchor_tail="他推开门走了进去。")
        self.assertFalse([i for i in issues if i.code == "G-ANCHOR"])

    def test_transition_word_is_flagged(self):
        issues, _ = check_chapter("# 标题\n第二天，主角醒了过来。" + "字" * 2400,
                                  chapter_num=2, target_words=2500)
        self.assertTrue(any(i.code == "G-TRANSITION" for i in issues))

    def test_transition_check_skips_heading(self):
        issues, _ = check_chapter("# 第2章 标题\n他推开门，屋里没有人。" + "字" * 2400,
                                  chapter_num=2, target_words=2500)
        self.assertFalse([i for i in issues if i.code == "G-TRANSITION"])

    def test_feedback_only_from_hard(self):
        issues, _ = check_chapter("# 第2章 标题\n他推开门走了进去。" + "字" * 2400,
                                  chapter_num=2, target_words=2500,
                                  anchor_tail="他推开门走了进去")
        self.assertEqual(feedback_from(issues), "", "只有 warn 时不得打回")

    def test_feedback_lists_hard_issues(self):
        issues, _ = check_chapter("短文。（本章完）", chapter_num=1, target_words=2500)
        text = feedback_from(issues)
        self.assertIn("客观质量闸未通过", text)
        self.assertIn("字数", text)


class TestGateDecision(unittest.TestCase):
    """闸门的决策表。这里藏过一个会让流水线卡死的 bug：放行时不清空 feedback，
    上一轮的打回意见会一直挂在 state 上，路由每轮判 failed 直到 max_retries。"""

    HARD = [Issue("G-WORDS", "hard", "字数超区间")]
    WARN = [Issue("G-TITLE", "warn", "标题重复章节号")]

    def test_pass_clears_feedback(self):
        self.assertEqual(gate_update([], mode="enforce", revision_count=2), {"feedback": ""})

    def test_hard_blocks_and_increments(self):
        out = gate_update(self.HARD, mode="enforce", revision_count=1)
        self.assertIn("字数超区间", out["feedback"])
        self.assertEqual(out["revision_count"], 2, "重写计数必须 +1，否则永远到不了 max_retries")

    def test_warn_only_passes(self):
        self.assertEqual(gate_update(self.WARN, mode="enforce", revision_count=3), {"feedback": ""})

    def test_dry_never_blocks(self):
        self.assertEqual(gate_update(self.HARD, mode="dry", revision_count=1), {"feedback": ""})

    def test_off_never_blocks(self):
        self.assertEqual(gate_update(self.HARD, mode="off", revision_count=1), {"feedback": ""})


class TestNormalize(unittest.TestCase):
    def test_strip_duplicate_chapter_number(self):
        res = normalize_chapter("# 第40章 捞金鱼赌约与暗涌的夏风\n正文", 40)
        self.assertTrue(res.text.startswith("# 捞金鱼赌约与暗涌的夏风"))
        self.assertIn("移除标题内重复的章节号", res.fixes)

    def test_strip_meta(self):
        res = normalize_chapter("正文。\n\n（本章完）", 1)
        self.assertNotIn("本章完", res.text)
        self.assertIn("移除场外元文本", res.fixes)

    def test_collapse_blank_lines(self):
        res = normalize_chapter("第一段\n\n\n\n第二段", 1)
        self.assertNotIn("\n\n\n", res.text)

    def test_keep_normal_title(self):
        res = normalize_chapter("# 春晨的煎蛋香\n正文", 1)
        self.assertTrue(res.text.startswith("# 春晨的煎蛋香"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
