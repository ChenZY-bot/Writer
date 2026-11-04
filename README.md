# 大文豪 Agent · Writer

> 基于 **LangGraph** 的中文连载小说自动化流水线：编剧 → 写手 → 客观闸门 → 审稿 → 记忆归档。
> 目标不是"一键生成一部小说"，而是**让机器写的每一章都能被核账**：谁埋的伏笔、哪章该还、
> 这段是不是抄了自己、这一章到底改过什么。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](#快速开始)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-1c3d5a.svg)](https://github.com/langchain-ai/langgraph)
[![Tests](https://img.shields.io/badge/tests-90%20passed-brightgreen.svg)](#测试)
[![License](https://img.shields.io/badge/license-未指定-lightgrey.svg)](#许可证)

**English** — A LangGraph-based Chinese web-novel writing pipeline with an objective
quality gate, an event/foreshadow ledger, and a hot-reloadable human-in-the-loop inbox.

---

## 它解决什么问题

自动写长篇，卡住人的从来不是"写不出来"，而是三件事：

| 问题 | 现象 | 本项目的做法 |
|---|---|---|
| **长剧情记忆** | 写到第 80 章，模型记不住第 12 章的设定；检索回来的全是同题材片段 | 5 章滑窗摘要 + Chroma 长期记忆；可选多路召回 + RRF/MMR 融合 |
| **伏笔自圆其说** | 坑越挖越多，没人记得该在第几章填 | 伏笔账本：埋设章 / 承诺回收章 / 逾期天数，到期顶到写手面前 |
| **剧情复读** | 每章都在"修罗场—和好—吃饭"；或通篇短句排比凑字数 | 三层机器判据：A 型死磕 / B 型短句轮换 / 跨章 8-gram 重合 |
| **人工监管** | 想插个角色、想纠偏，只能改代码重跑 | 带类型的指令收件箱，**每章热读**，落 canon，可审计、可回滚 |

---

## 架构

```
                    ┌──────────────── 每章热读 ────────────────┐
                    │ config/directives/inbox.jsonl            │
                    │  insert_character / lock_fact /          │
                    │  steer_plot / ban_motif / rewrite_chapter│
                    └───────────────┬──────────────────────────┘
                                    ▼
config/outline.txt ──┐      ┌────────────────┐
memory/glossary.json ┼─────▶│ main: 拼注入块  │──▶ director_notes
memory/foreshadow.json┤      │ canon/伏笔/禁写 │      （planner & writer 已消费此字段）
banned_motifs.json ──┘      └────────────────┘
                                    │
   ┌─────────┐   ┌────────┐   ┌─────────┐   ┌──────────┐   ┌────────┐
   │ Planner │──▶│ Writer │──▶│  Gate   │──▶│ Reviewer │──▶│ Memory │
   └─────────┘   └────────┘   └────┬────┘   └──────────┘   └───┬────┘
        ▲                           │ failed                    │
        │                           ▼                           ▼
        │                      back to Writer          EventLedger（去重/封顶）
        └───────────────────────────────────────────   Chroma（切块入库）
                                                     context_memory（5 章窗口）
```

四个 Agent 各自一份 prompt（`prompt/*.txt`），模型走**火山引擎 Ark**（OpenAI 兼容协议），
词嵌入走**本地 Ollama `bge-m3`**。

---

## 核心设计

### 1. Gate：把"能机器判的"从 LLM 审稿里摘出来

纯 LLM 当唯一关卡有个已知毛病：它会盖章。本项目的闸门只做**能给出证据**的判断：

| 判据 | 说明 | 级别 |
|---|---|---|
| `G-WORDS` | 字数偏离目标（汉字 + 英文词，不含标点） | hard |
| `G-META` | 正文里的"本章完/下章预告/全文字数" | hard |
| `G-REPEAT-A` | 同一句死磕（单句占比 ≥20% 且次数 ≥8） | hard |
| `G-REPEAT-B` | 短句轮换（极短句 ≥35% 且 Top5 句式 ≥50%） | hard |
| `G-REPEAT-TAIL` | 尾部 4000 字单独复检 | hard |
| `G-CROSS-DUP` | 与最近两章 8-gram 重合 ≥12% | hard |
| `G-MOTIF-BANNED` | 命中人工禁写桥段 | hard |
| `G-WHITELIST` | 细纲白名单外的人物出场（需提供花名册） | hard |
| `G-TITLE` / `G-ANCHOR` / `G-TRANSITION` | 标题格式 / 上章话没说完却没接上 / 开篇"第二天"式强行转场 | warn |

三档模式：`off` / `dry`（只记录不拦截）/ `enforce`（打回写手重写）。

**判据必须能失败。** `tests/test_quality.py` 里每条 hard 判据都有用例，并且专门有一条
`test_b_type_short_rotation_not_caught_by_a` 证明"只做 A 型会漏掉 B 型"。

### 2. 事件账本：让"防撞梗雷达"收敛

旧实现把事件表当纯文本：模型不主动写"事件结束"，列表就只增不减。实测跑到第 88 章时
"进行中"堆了 121 条，而审稿判据是"只有重演【已完结】事件才打回"——**主关卡在结构上失效**。

账本做了四件事：解析/渲染（兼容旧文本，可无损迁移）、**去重**（同义与包含关系合并）、
**封顶**（超出预算的归档到一行名字清单，仍参与防撞梗）、**生命周期**（开启/结束/归档都有章节号）。

### 3. 伏笔账本：把"挖坑"和"填坑"变成两条可对账的记录

```
FS-012-01《木剑的裂痕》；第12章埋设；承诺第20章前回收，已逾期3章；埋设原文：……
```

`render_block()` 只把**本章到期/逾期**的伏笔塞进 prompt（有字数预算），并要求写手：
回收的必须交代，不回收的必须在细纲里写明推迟理由与新的回收章。

### 4. 多路召回 + 融合排序（默认关）

单路 `similarity_search(chapter_plan)` 的问题是"计划文本"和"已写正文"天然不像。
可选模式会同时跑：细纲路、角色/设定路、到期伏笔路，再用

* **RRF**（k=60，只看名次，免疫量纲）
* **三因子**（recency × importance × relevance）
* **MMR**（λ=0.7，防止两条几乎一样的片段占两格）
* **去重**（3-gram Jaccard + 包含关系）

融合后按配额截断。开关默认关，观察一轮再开。

### 5. 热更新与人工在环

流水线**每章开头重读一次** `config/directives/inbox.jsonl`。进程在跑的时候追加一行，
下一章就生效，不用重启：

```jsonl
{"id":"D-001","type":"insert_character","payload":{"name":"示例角色","setting":"旧山神社巫女"}}
{"id":"D-002","type":"ban_motif","payload":{"motif":"学园祭"}}
{"id":"D-003","type":"rewrite_chapter","payload":{"chapter":88,"confirm":true}}
```

详见 [`config/directives/README.md`](config/directives/README.md)。

---

## 快速开始

```bash
git clone <your-repo-url> writer
cd writer

python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 1) 配置（这两个文件不会被提交）
cp config/agent_config.example.yml config/agent_config.yml
cp config/settings_config.example.yml config/settings_config.yml
#    填 volc_api_key / chat_model_name；没有 Key 也可以改用环境变量 ARK_API_KEY

# 2) 本地向量模型
ollama pull bge-m3        # 并确保 ollama 在 11434 端口运行

# 3) 写大纲
#    编辑 config/outline.txt（人物设定、故事基调、章节规划）

# 4) 开写
python main.py
```

单章调试：

```bash
python -m agent.nodes.writer_node     # 只跑写手
python -m agent.nodes.planner_node    # 只跑编剧
python -m agent.nodes.gate_node       # 只跑闸门（需要草稿）
```

> ⚠️ 第一次运行建议保持 `quality_gate: "dry"`：先看 `logs/Gate_*.log` 的命中率，
> 确认判据对你的文风不误伤，再改成 `enforce`。

---

## 配置

| 文件 | 是否入库 | 作用 |
|---|---|---|
| `config/agent_config.yml` | ❌ 已忽略 | API Key、模型接入点、Ollama 地址 |
| `config/settings_config.yml` | ❌ 已忽略 | 字数、重试、输出目录、功能开关 |
| `config/outline.txt` | ❌ 已忽略 | 你的作品大纲 |
| `config/directives/inbox.jsonl` | ❌ 已忽略 | 人工干预指令流水 |
| `config/prompt_config.yml` | ✅ | prompt 文件名映射 |
| `config/chroma_config.yml` | ✅ | 向量库路径、collection、top_k |
| `config/*.example.yml` | ✅ | 上面两个被忽略文件的模板 |

功能开关（`settings_config.yml` → `features`）：

| 开关 | 默认 | 说明 |
|---|---|---|
| `normalize_output` | `true` | 只修格式：去元文本、去标题里重复的章节号、合并空行 |
| `quality_gate` | `"dry"` | `off` / `dry` / `enforce` |
| `events_ledger` | `true` | 事件账本（去重 + 封顶 + 归档） |
| `fused_retrieval` | `false` | 多路召回 + RRF/MMR |
| `retrieval_factors` | `false` | 融合后的时效/重要度加权 |
| `foreshadow_block` | `true` | 到期伏笔注入 |
| `directives` | `true` | 每章热读指令收件箱 |

---

## 目录结构

```
agent/
  state.py            全局状态唯一真源（原先五个节点各写一份、字段互相漂移）
  workflow_graph.py   LangGraph 流程图与路由
  nodes/              planner / writer / gate / reviewer / memory
  canon/              事件账本、伏笔账本（可对账的硬事实层）
  quality/            复读判据、客观闸门、格式归一化
  memory/             多路召回融合（RRF / 三因子 / MMR）
  hitl/               人工干预指令协议与 canon 存储
  tools/              检索工具
model/factory.py      模型工厂（Ark Chat + Ollama Embeddings）
prompt/               四个 Agent 各自的 prompt
utils/                配置、日志、文件、prompt 加载
config/               配置与人工指令（敏感项见 .gitignore）
docs/                 设计文档
tests/                纯逻辑单测（不依赖网络与模型）
main.py               入口：断点续写、人工指令注入、落盘
```

运行时自动创建、且**不入库**的目录：`output/`（正文）、`memory/`（存档与向量库）、`logs/`。

---

## 测试

```bash
python -m unittest discover -s tests -t .
# Ran 90 tests ... OK
```

测试只覆盖**纯逻辑**（账本、判据、融合、指令），不需要 API Key、不需要网络、不需要向量库。
覆盖的回归点包括：

* 事件去重/封顶/渲染预算、旧文本迁移的幂等性
* A/B 型复读、纯标点单元、尾部复检、跨章重复、禁写桥段
* 字数/标题/锚点/转场/白名单
* RRF、MMR 多样性、去重保留更全文本、三因子的零值保护
* 指令校验、幂等、canon 落盘、回滚请求
* 闸门决策表（放行必须清空 feedback，否则路由会卡到 `max_retries`）

---

## 已知限制

1. **单作品、单 collection**：`memory/chroma_db` 不按作品隔离，换书前请清库。
2. **回滚重写不回滚向量库**：`rewrite_chapter` 只归档正文文件与章节号，被作废章节的片段仍在向量库里。
3. **闸门阈值需要在你的语料上校准一轮**：默认值来自中文网文语料，不同文风可能需要调。
4. **`G-WHITELIST` 默认跳过**：没有花名册时会写进 `skipped`，不会假装通过。
5. **没有 Web UI**：人工干预是文件 + CLI；断点续跑靠 `memory/state.json`。
6. **强依赖本地 Ollama**：无 embedding 服务时长期记忆检索会降级为失败提示。
7. **未做多机并发**：单机单进程设计，没有文件锁。

---

## 隐私与数据边界

本仓库**只包含代码与通用 prompt**。以下内容被 `.gitignore` 明确排除，不会被提交：

* `output/`、`memory/`、`logs/` —— 正文、存档、向量库、日志
* `config/agent_config.yml` —— API Key 与模型接入点
* `config/settings_config.yml` —— 本机路径与作品输出目录
* `config/outline.txt`、`config/director_notes.txt`、`config/directives/*.jsonl` —— 作品设定与人工决策

如果你 fork 后要公开，**先检查 `git log` 里有没有历史提交带过这些文件**。

---

## 参考与致谢

两个开源项目直接影响了本项目的设计：

* **[dsh-degeneration-kit](https://github.com/walksls/dsh-degeneration-kit)** —— 复读分型
  （A 型同一句死磕 / B 型短句轮换）与"判据必须能失败"的工程纪律。
  `agent/quality/repetition.py` 的双判据迁移自其 `thinking-guard/lib/detect-v2.mjs`。
* **[fish（大肥鱼生命系统）](https://github.com/kinganyi/fish)** —— 记忆的编码、召回与推送：
  RRF / 三因子 / MMR / 价值门、内容感知去重、注入预算。
  `agent/memory/fusion.py` 的融合流水线参考其 `p0/fish_push_rank.py`。

设计取舍与实测数据见 [`docs/优化方案-基于两个开源库.md`](docs/优化方案-基于两个开源库.md)。

---

## 许可证

本项目**暂未选择开源许可证**。在补充 `LICENSE` 之前，默认保留所有权利；
如需引用或二次分发，请先联系作者。
