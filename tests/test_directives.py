"""人工干预指令的判据：合法性、幂等、canon 落盘、回滚请求。"""
import json
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.hitl.directives import (
    CanonStore, Directive, apply_directives, enqueue, load_pending, mark_applied,
)

_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp")


def tmp_file(name: str) -> str:
    os.makedirs(_TMP_ROOT, exist_ok=True)
    return os.path.join(_TMP_ROOT, f"{uuid.uuid4().hex[:8]}_{name}")


def remove_quietly(*paths: str) -> None:
    for p in paths:
        if p and os.path.exists(p):
            os.remove(p)


class TestValidate(unittest.TestCase):
    def test_unknown_type_rejected(self):
        self.assertTrue(Directive(id="D1", type="nuke").validate())

    def test_insert_character_needs_name(self):
        self.assertTrue(Directive(id="D1", type="insert_character", payload={}).validate())
        self.assertEqual(
            Directive(id="D1", type="insert_character", payload={"name": "新角色"}).validate(), [])

    def test_rewrite_requires_confirm(self):
        d = Directive(id="D1", type="rewrite_chapter", payload={"chapter": 88})
        self.assertTrue(any("confirm" in e for e in d.validate()))
        d.payload["confirm"] = True
        self.assertEqual(d.validate(), [])

    def test_rewrite_requires_positive_chapter(self):
        d = Directive(id="D1", type="rewrite_chapter", payload={"chapter": 0, "confirm": True})
        self.assertTrue(d.validate())


class TestApply(unittest.TestCase):
    def setUp(self):
        self.glossary = tmp_file("glossary.json")
        self.banned = tmp_file("banned.json")
        self.canon = CanonStore.load(self.glossary, self.banned)

    def tearDown(self):
        remove_quietly(self.glossary, self.banned)

    def test_insert_character_goes_to_canon(self):
        res = apply_directives(
            [Directive("D1", "insert_character", {"name": "新角色", "setting": "山间神社巫女"})],
            chapter=89, canon=self.canon)
        self.assertEqual(self.canon.characters["新角色"], "山间神社巫女")
        self.assertIn("D1", res.consumed)
        self.assertIn("新角色", res.render_notes())

    def test_lock_fact(self):
        apply_directives([Directive("D2", "lock_fact", {"text": "主角不能飞"})],
                         chapter=89, canon=self.canon)
        self.assertEqual(self.canon.facts[0]["text"], "主角不能飞")
        self.assertIn("主角不能飞", self.canon.render_block())

    def test_ban_motif_dedup(self):
        apply_directives([Directive("D3", "ban_motif", {"motif": "学园祭"}),
                          Directive("D4", "ban_motif", {"motif": "学园祭"})],
                         chapter=89, canon=self.canon)
        self.assertEqual(self.canon.banned, ["学园祭"])

    def test_steer_plot_is_one_shot_note(self):
        res = apply_directives([Directive("D5", "steer_plot", {"text": "让角色C在本章摊牌"})],
                               chapter=89, canon=self.canon)
        self.assertIn("摊牌", res.render_notes())

    def test_rewrite_sets_rewind(self):
        res = apply_directives(
            [Directive("D6", "rewrite_chapter", {"chapter": 88, "confirm": True})],
            chapter=89, canon=self.canon)
        self.assertEqual(res.rewind_chapter, 88)

    def test_canon_survives_save_load(self):
        apply_directives([Directive("D1", "insert_character", {"name": "A", "setting": "B"})],
                         chapter=1, canon=self.canon)
        self.canon.save()
        again = CanonStore.load(self.glossary, self.banned)
        self.assertEqual(again.characters, {"A": "B"})

    def test_render_notes_empty_when_nothing(self):
        res = apply_directives([], chapter=1, canon=self.canon)
        self.assertEqual(res.render_notes(), "")


class TestInbox(unittest.TestCase):
    def setUp(self):
        self.inbox = tmp_file("inbox.jsonl")
        self.applied = tmp_file("applied.jsonl")

    def tearDown(self):
        remove_quietly(self.inbox, self.applied)

    def test_enqueue_then_load(self):
        enqueue(Directive("D1", "ban_motif", {"motif": "烟火大会"}), self.inbox)
        pending, errors = load_pending(self.inbox, self.applied)
        self.assertEqual(errors, [])
        self.assertEqual([d.id for d in pending], ["D1"])

    def test_applied_is_idempotent(self):
        enqueue(Directive("D1", "ban_motif", {"motif": "烟火大会"}), self.inbox)
        pending, _ = load_pending(self.inbox, self.applied)
        mark_applied(self.applied, pending, chapter=89)
        again, _ = load_pending(self.inbox, self.applied)
        self.assertEqual(again, [], "已生效的指令不得重复执行")

    def test_broken_json_is_reported_not_swallowed(self):
        with open(self.inbox, "w", encoding="utf-8") as f:
            f.write('{"id":"D1","type":"ban_motif","payload":{"motif":"烟火大会"}}\n{oops\n')
        pending, errors = load_pending(self.inbox, self.applied)
        self.assertEqual(len(pending), 1)
        self.assertTrue(errors, "坏行必须显式报错")

    def test_invalid_directive_not_returned(self):
        with open(self.inbox, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "D9", "type": "rewrite_chapter",
                                "payload": {"chapter": 88}}) + "\n")
        pending, errors = load_pending(self.inbox, self.applied)
        self.assertEqual(pending, [])
        self.assertTrue(errors)

    def test_enqueue_rejects_invalid(self):
        with self.assertRaises(ValueError):
            enqueue(Directive("D1", "unknown", {}), self.inbox)


if __name__ == "__main__":
    unittest.main(verbosity=2)
