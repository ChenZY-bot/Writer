import hashlib
import os
import sys

# 自动获取项目根目录并将其加入系统路径，解决独立运行时的包导入问题
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langchain_chroma import Chroma
from model.factory import EmbeddingsFactory
from utils.logger_handler import sys_logger
from utils.config_handler import chroma_config, settings_config
from agent.memory.fusion import Candidate, select


def _doc_id(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def _search_route(vectorstore, query: str, k: int, route: str) -> list[Candidate]:
    """一路检索 -> Candidate。Chroma 返回的是距离（越小越像），这里转成 0~1 的相似度。"""
    try:
        pairs = vectorstore.similarity_search_with_score(query, k=k)
    except Exception as e:                      # noqa: BLE001 —— 单路失败不该拖垮整次检索
        sys_logger.warning(f"检索路「{route}」失败：{e}")
        return []
    out: list[Candidate] = []
    for doc, distance in pairs:
        text = (doc.page_content or "").strip()
        if not text:
            continue
        score = 1.0 / (1.0 + max(0.0, float(distance)))
        chapter = doc.metadata.get("chapter")
        out.append(Candidate(
            id=f"ch{chapter}-{_doc_id(text)}",
            text=text,
            route=route,
            score=score,
            chapter=int(chapter) if isinstance(chapter, (int, float, str)) and str(chapter).isdigit() else None,
        ))
    return out


def retrieve_long_term_memory(query: str, top_k: int = None, *,
                              chapter_num: int | None = None,
                              extra_queries: list[str] | None = None) -> str:
    """
    根据给定的查询词（如本章大纲），从 ChromaDB 中检索相关的长期记忆（伏笔、实体设定等）

    :param query: 搜索查询词（建议传入大纲中当前章节的设定）
    :param top_k: 期望返回的相关文本块数量，若不传则默认读取 chroma.yml 中的 top_k 配置，兜底为 3
    :param chapter_num: 当前章节号，开启融合检索时用于时效加权
    :param extra_queries: 额外检索路（角色名、到期伏笔标题等），仅融合模式使用
    :return: 格式化后的长期记忆字符串
    """
    # 动态从配置文件获取 top_k，允许函数传参覆盖
    actual_top_k = top_k or chroma_config.get("top_k", 3)
    features = settings_config.get("features") or {}
    fused_mode = bool(features.get("fused_retrieval", False))

    sys_logger.debug(f"开始检索长期记忆，关键词：'{query[:20]}...'，期望返回 {actual_top_k} 条相关文本")

    chroma_dir_name = chroma_config.get("persist_directory", "memory/chroma_db")
    persist_dir = os.path.join(project_root, chroma_dir_name)

    # 1. 冷启动保护：如果数据库目录不存在，说明这是小说初期，直接返回空
    if not os.path.exists(persist_dir) or not os.listdir(persist_dir):
        sys_logger.info("向量数据库尚未建立或为空，当前无长期记忆。")
        return "当前为故事初期，暂无需要参考的长期记忆片段。"

    try:
        # 2. 实例化词嵌入模型与向量库
        embed_model = EmbeddingsFactory().create_model()
        vectorstore = Chroma(
            persist_directory=persist_dir,
            embedding_function=embed_model,
            collection_name=chroma_config.get("collection_name", "novel_memory")
        )

        if not fused_mode:
            # ---- 原始单路检索（默认，行为不变）----
            docs = vectorstore.similarity_search(query, k=actual_top_k)
            if not docs:
                return "未检索到与当前情节强相关的历史记忆。"
            formatted_memories = []
            for doc in docs:
                source_chapter = doc.metadata.get('chapter', '未知')
                memory_text = doc.page_content.strip()
                formatted_memories.append(f"[历史来源: 第 {source_chapter} 章] {memory_text}")
            final_result = "\n\n".join(formatted_memories)
            sys_logger.info(f"成功检索到 {len(docs)} 条相关长期记忆片段。")
            return final_result

        # ---- 多路召回 + 融合（RRF / 去重 / MMR）----
        # 为什么要多路：细纲文本和正文片段的相似度天然偏低，单路捞回来的常常是
        # 同题材的其他章节，而不是"这章要用的那个设定"。
        routes: dict[str, list[Candidate]] = {
            "vector": _search_route(vectorstore, query, actual_top_k * 2, "vector")
        }
        for i, q in enumerate(extra_queries or []):
            if not q or not q.strip():
                continue
            routes[f"extra{i}"] = _search_route(vectorstore, q, 3, "extra")

        merged = select(
            routes,
            quota=actual_top_k,
            current_chapter=chapter_num or 1,
            use_factors=bool(features.get("retrieval_factors", False)),
            use_mmr=True,
        )
        if not merged:
            return "未检索到与当前情节强相关的历史记忆。"

        formatted = [
            f"[历史来源: 第 {c.chapter if c.chapter is not None else '未知'} 章] {c.text.strip()}"
            for c in merged
        ]
        sys_logger.info(
            f"融合检索：{len(routes)} 路 -> {sum(len(v) for v in routes.values())} 条候选 -> "
            f"{len(merged)} 条（去重+MMR 后）"
        )
        return "\n\n".join(formatted)

    except Exception as e:
        sys_logger.error(f"检索长期记忆时发生错误: {str(e)}")
        return "检索历史记忆失败，请仅依据当前工作记忆进行创作。"


if __name__ == "__main__":
    # 独立测试检索工具
    # 注意：运行此测试前，请确保你已经成功运行过 memory_node 的测试（已经生成了 chroma_db 文件夹）
    test_query = "叶凡和镇长李霸天有什么恩怨？枯木变成了什么？"

    print("\n" + "="*40 + " 长期记忆检索测试 " + "="*40)
    result = retrieve_long_term_memory(test_query, top_k=2)
    print("检索结果:\n")
    print(result)
    print("="*96)
