# 人工干预收件箱（热更新）

流水线**每章开头重新读一次** `inbox.jsonl`。所以进程在跑的时候，你往这里追加一行，
下一章就生效，**不用重启**。

一行一个 JSON。生效过的指令记到 `applied.jsonl`，按 `id` 幂等——重复投放不会重复执行。

## 支持的指令

```jsonl
{"id":"D-001","type":"insert_character","payload":{"name":"示例角色","setting":"旧山神社巫女，三年前救过主角"},"note":"加强配角线"}
{"id":"D-002","type":"lock_fact","payload":{"text":"主角不能飞，也不能用灵能远程攻击"}}
{"id":"D-003","type":"steer_plot","payload":{"text":"本章让妹妹第一次主动开口求助"},"note":"角色成长"}
{"id":"D-004","type":"ban_motif","payload":{"motif":"学园祭"}}
{"id":"D-005","type":"rewrite_chapter","payload":{"chapter":88,"confirm":true},"note":"第88章人物 OOC"}
```

| type | 作用 | 生效范围 |
|---|---|---|
| `insert_character` | 写进 `memory/glossary.json`，**每章都注入** | 永久 |
| `lock_fact` | 锁定事实，写手/审稿都看得到 | 永久 |
| `steer_plot` | 一次性纠偏 | 仅下一章 |
| `ban_motif` | 加入 `memory/banned_motifs.json`，闸门会拦 | 永久 |
| `rewrite_chapter` | 把第 N 章及之后归档到 `output/_archive_*`，从 N 重写 | 一次 |

## 命令行

```powershell
# 追加一条（也可以手写 inbox.jsonl）
python -m agent.hitl.directives add ban_motif --id D-006 --payload '{"motif":"烟火大会"}'

# 看待生效的指令
python -m agent.hitl.directives list
```

## 注意

* `rewrite_chapter` 必须带 `"confirm": true`，防止手滑回滚。
* 回滚**只回滚正文文件与章节号**；向量库和事件账本不会自动回滚，需要时请手动重建
  （见 `docs/优化方案-基于两个开源库.md` 的「已知限制」）。
* 指令文件用 UTF-8 保存，一行必须是一个完整 JSON，不允许跨行。
