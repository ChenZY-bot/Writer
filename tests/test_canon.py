"""事件账本与伏笔账本的判据。

每条测试对应一个真实缺陷：
* test_dedupe_merges_real_duplicates —— state.json 里"反派家族突袭名门家"有 4 种写法
* test_budget_archives_oldest —— 第 88 章时"进行中"堆到了 121 条
* test_roundtrip_is_stable —— 迁移必须无损，不能把旧存档读坏
"""
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.canon.events import EventLedger, normalize_name
from agent.canon.foreshadow import ForeshadowLedger

# 沙箱只允许写工作区：临时目录必须落在 tests/.tmp 下，不能用系统 TEMP。
# 注意：不要用 tempfile.mkdtemp —— 沙箱下新建目录的 DACL 会让进程自己写不进去。
_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp")


def tmp_file(name: str) -> str:
    """直接落在 tests/.tmp 下的唯一文件名。

    不建子目录：沙箱给 `.tmp` 继承了 `Everyone:(CI)(DC)` 的 DENY，子目录建得出来却删不掉，
    测试跑完会攒垃圾。文件级清理没有这个问题。
    """
    os.makedirs(_TMP_ROOT, exist_ok=True)
    return os.path.join(_TMP_ROOT, f"{uuid.uuid4().hex[:8]}_{name}")


LEGACY = """【已完结的历史大事件（绝对禁止重复）】
- 第82章起：虚无海封印破损危机 (至第83章完)

【当前进行中的篇章/事件（允许且应当接续发展）】
- 第1章起：角色A转入高二B班
- 第2章起：角色A转学入校
- 第2章起：反派家族武装突袭名门家族总部
- 第3章起：反派家族武装突袭名门家
- 第39章起：夏日祭捞金鱼赌约
- 第40章起：夏日祭捞金鱼赌约
- 第70章起：反派夺魂成神行动
"""


class TestEvents(unittest.TestCase):
    def test_parse_legacy_text(self):
        led = EventLedger.from_text(LEGACY)
        self.assertEqual(len(led.events), 8)
        self.assertEqual(led.stats()["closed"], 1)
        self.assertEqual(led.stats()["open"], 7)

    def test_entries_without_chapter_are_kept(self):
        led = EventLedger.from_text("- 无章节号的事件")
        self.assertEqual(len(led.events), 1)
        self.assertEqual(led.events[0].start, 0)

    def test_dedupe_merges_real_duplicates(self):
        led = EventLedger.from_text(LEGACY)
        removed = led.dedupe()
        self.assertGreaterEqual(removed, 2, "同义写法的重复事件必须被合并")
        names = [e.name for e in led.events]
        # 保留更具体的那种写法
        self.assertTrue(any("名门家族总部" in n for n in names))

    def test_dedupe_keeps_distinct_events(self):
        led = EventLedger.from_text("- 第1章起：学园祭\n- 第1章起：夏日祭")
        led.dedupe()
        self.assertEqual(len(led.events), 2)

    def test_dedupe_does_not_merge_short_names(self):
        # 2~5 字的名字不做包含合并，否则"樱"会吃掉"樱花祭"
        led = EventLedger.from_text("- 第1章起：樱\n- 第2章起：樱花祭")
        led.dedupe()
        self.assertEqual(len(led.events), 2)

    def test_budget_archives_oldest(self):
        names = "\n".join(f"- 第{i}章起：事件{i}" for i in range(1, 31))
        led = EventLedger.from_text(names)
        archived = led.enforce_budget(current_chapter=88, max_ongoing=15)
        self.assertEqual(len(archived), 15)
        self.assertEqual(led.stats()["open"], 15)
        self.assertEqual(led.stats()["archived"], 15)
        # 归档的必须是最老的，且仍以"名字清单"的形式参与防撞梗
        self.assertTrue(all(ev.start <= 15 for ev in archived))
        text = led.to_text()
        self.assertIn("已归档", text)
        self.assertIn("事件1", text)
        self.assertLess(len(text), 2000, "归档区必须压缩，不能把 121 条全展开")

    def test_budget_noop_when_under_cap(self):
        led = EventLedger.from_text("- 第1章起：A\n- 第2章起：B")
        self.assertEqual(led.enforce_budget(10, max_ongoing=5), [])

    def test_close_event_by_partial_name(self):
        led = EventLedger.from_text("- 第10章起：夏日祭捞金鱼赌约")
        ev = led.close_event("捞金鱼赌约", 12)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.end, 12)

    def test_close_unknown_returns_none(self):
        led = EventLedger.from_text("- 第10章起：夏日祭")
        self.assertIsNone(led.close_event("不存在的活动", 12))

    def test_apply_tags_multi_line(self):
        led = EventLedger.from_text("- 第10章起：夏日祭")
        out = led.apply_tags("开启新事件：学园祭\n事件继续：夏日祭\n事件结束：夏日祭", 11)
        self.assertEqual(out["opened"], ["学园祭"])
        self.assertEqual(out["closed"], ["夏日祭"])
        names = {e.name: e for e in led.events}
        self.assertEqual(names["夏日祭"].end, 11)
        self.assertTrue(names["学园祭"].is_open)

    def test_apply_tags_unknown_is_reported(self):
        led = EventLedger.from_text("- 第10章起：夏日祭")
        out = led.apply_tags("事件结束：早已完结的活动", 11)
        self.assertEqual(len(out["unknown"]), 1)

    def test_roundtrip_is_stable(self):
        led = EventLedger.from_text(LEGACY)
        led.dedupe()
        text = led.to_text()
        again = EventLedger.from_text(text)
        self.assertEqual(text, again.to_text(), "渲染->解析->渲染 必须幂等")

    def test_json_roundtrip(self):
        led = EventLedger.from_text(LEGACY)
        led.dedupe()
        restored = EventLedger.from_text(led.to_json())
        self.assertEqual(led.to_text(), restored.to_text())

    def test_broken_json_falls_back_to_text(self):
        led = EventLedger.from_text('{"events": [这项不是 JSON')
        self.assertTrue(led.unparsed, "解析失败必须被记录，不能静默")

    def test_normalize_name_strips_punctuation(self):
        self.assertEqual(normalize_name("第3章起：【渡边家】突袭"), normalize_name("渡边家突袭"))


class TestForeshadow(unittest.TestCase):
    def setUp(self):
        self.led = ForeshadowLedger()

    def test_add_and_due(self):
        self.led.add("铜镜的裂痕", 12, due=20, evidence="剑身裂了一道缝")
        self.assertEqual(self.led.due(19), [])
        due = self.led.due(23)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].overdue(23), 3)

    def test_pay_requires_open(self):
        t = self.led.add("玉佩", 5, due=10)
        self.assertIsNotNone(self.led.pay(t.id, 9, "玉佩认主"))
        self.assertIsNone(self.led.pay(t.id, 10, "重复回收"))

    def test_paid_thread_not_due(self):
        t = self.led.add("玉佩", 5, due=10)
        self.led.pay(t.id, 9)
        self.assertEqual(self.led.due(20), [])

    def test_due_sorted_by_overdue_then_priority(self):
        self.led.add("A", 1, due=10, priority=2)
        self.led.add("B", 1, due=5, priority=2)
        self.led.add("C", 1, due=10, priority=1)
        titles = [t.title for t in self.led.due(20)]
        self.assertEqual(titles[0], "B")     # 逾期最多
        self.assertEqual(titles[1], "C")     # 同逾期，优先级高

    def test_render_block_bounded(self):
        for i in range(20):
            self.led.add(f"伏笔{i}", 1, due=2)
        block = self.led.render_block(30, max_items=6, max_chars=900)
        self.assertLessEqual(block.count("\n- "), 8)
        self.assertIn("另有", block)

    def test_render_block_empty_when_nothing_due(self):
        self.led.add("未来伏笔", 1, due=99)
        self.assertEqual(self.led.render_block(10), "")

    def test_persistence_roundtrip(self):
        path = tmp_file("foreshadow.json")
        try:
            led = ForeshadowLedger.load(path)
            led.add("铜镜", 12, due=20, owner="叶凡")
            led.save()
            again = ForeshadowLedger.load(path)
            self.assertEqual(len(again.threads), 1)
            self.assertEqual(again.threads[0].owner, "叶凡")
            self.assertEqual(again.threads[0].due, 20)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_corrupt_file_loads_empty(self):
        path = tmp_file("foreshadow.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("{坏文件")
            self.assertEqual(len(ForeshadowLedger.load(path).threads), 0)
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
