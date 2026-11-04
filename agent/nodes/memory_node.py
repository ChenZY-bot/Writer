import os
import sys
import re

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langchain_core.prompts import PromptTemplate
from langchain_core.messages import HumanMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma

from model.factory import ChatModelFactory, EmbeddingsFactory
from utils.logger_handler import memory_logger
from utils.prompt_loader import load_prompt
from utils.config_handler import chroma_config, settings_config
from agent.state import AgentState

def memory_node(state: AgentState) -> dict:
    memory_logger.info(f"=== 开始处理 第 {state['current_chapter_num']} 章的记忆归档 ===")
    
    draft = state.get("draft_content", "")
    if not draft:
        memory_logger.warning("草稿内容为空，跳过记忆处理。")
        return {}
        
    memory_logger.debug("正在生成本章摘要与大事件状态...")
    llm = ChatModelFactory().create_model(temperature=0.3)
    prompt_text = load_prompt("memory_prompt_path")
    
    prompt_template = PromptTemplate(
        input_variables=["chapter_content"],
        template=prompt_text
    )
    
    messages = [HumanMessage(content=prompt_template.format(chapter_content=draft))]
    response = llm.invoke(messages)
    full_response = response.content.strip()
    
    # ==========================================
    # 【强力解析模块】分离普通摘要与大事件状态
    # ==========================================
    chapter_summary = full_response
    clean_tag = ""
    
    # 兼容“全局大事件标签”或“全局大事件状态”两种提法
    if "全局大事件" in full_response:
        parts = re.split(r'【?全局大事件[标签状态]+】?', full_response)
        if len(parts) > 1:
            chapter_summary = parts[0].strip()
            raw_tag = parts[1].strip()
            # 清洗多余的 Markdown 符号
            clean_tag = re.sub(r'^[:：#\*\-\s]+', '', raw_tag).strip()
    else:
        memory_logger.warning("⚠️ 未在模型输出中找到【全局大事件状态】。")

    # ==========================================
    # 【核心升级：支持多事件并行的生命周期管理】
    # ==========================================
    current_global_events = state.get("global_events", "")
    past_events = []
    ongoing_events = []
    current_mode = "past" 
    
    # 解析当前的事件列表
    for line in current_global_events.split('\n'):
        line = line.strip()
        if not line: continue
        if "已完结" in line:
            current_mode = "past"
        elif "当前进行中" in line:
            current_mode = "ongoing"
        elif line.startswith("- "):
            if current_mode == "past":
                past_events.append(line)
            elif current_mode == "ongoing":
                ongoing_events.append(line)

    # 逐行处理大模型给出的多条状态指令（支持多线并行）
    if clean_tag and clean_tag != "无":
        for line in clean_tag.split('\n'):
            line = line.strip()
            if not line or line == "无":
                continue
                
            # 指令1：开启新事件
            if "开启新事件" in line or "新事件开启" in line:
                event_name = re.sub(r'.*?(开启新事件|新事件开启)[：:]?\s*', '', line).strip()
                if event_name:
                    ongoing_events.append(f"- 第{state['current_chapter_num']}章起：{event_name}")
                    memory_logger.info(f"🏷️ 开启新事件：【{event_name}】")
            
            # 指令2：特定事件结束
            elif "事件结束" in line:
                event_name = re.sub(r'.*?(事件结束)[：:]?\s*', '', line).strip()
                if event_name:
                    # 尝试在正在进行的事件中寻找它并移除
                    matched_idx = -1
                    for i, og_event in enumerate(ongoing_events):
                        # 只要名字部分匹配上就算命中
                        if event_name in og_event or og_event.split("：")[-1].strip() in event_name:
                            matched_idx = i
                            break
                    
                    if matched_idx != -1:
                        ended_event = ongoing_events.pop(matched_idx)
                        # 追加完结章数标记
                        ended_event += f" (至第{state['current_chapter_num']}章完)"
                        past_events.append(ended_event)
                        memory_logger.info(f"🏷️ 事件结束入档：【{ended_event}】")
                    else:
                        memory_logger.warning(f"⚠️ 无法结束事件【{event_name}】，因其不在进行中列表中。")
                elif ongoing_events:
                    # 如果没写名字，保守处理，结束最早开启的那个
                    ended_event = ongoing_events.pop(0)
                    ended_event += f" (至第{state['current_chapter_num']}章完)"
                    past_events.append(ended_event)
                    memory_logger.info(f"🏷️ 默认结束事件：【{ended_event}】")
            
            # 指令3：事件继续（占位符，不做列表变更）
            elif "事件继续" in line:
                event_name = re.sub(r'.*?(事件继续)[：:]?\s*', '', line).strip()
                memory_logger.info(f"🏷️ 并行事件继续：【{event_name if event_name else '多线事件'}】")
                
            # 指令4：日常过渡
            elif "日常过渡" in line:
                memory_logger.info("🏷️ 日常过渡状态。")
                
            else:
                # 兼容老格式或其他未格式化的标签，直接作为新事件推入
                if not any(line in og for og in ongoing_events) and len(line) < 20:
                    ongoing_events.append(f"- 第{state['current_chapter_num']}章起：{line}")
                    memory_logger.info(f"🏷️ 记录并行事件：【{line}】")
    else:
        memory_logger.info("🏷️ 本章大事件状态无变动。")

    # 重新编排输出格式
    new_global_events_parts = []
    if past_events:
        new_global_events_parts.append("【已完结的历史大事件（绝对禁止重复）】")
        new_global_events_parts.extend(past_events)
        
    if ongoing_events:
        new_global_events_parts.append("\n【当前进行中的篇章/事件（允许且应当接续发展）】")
        new_global_events_parts.extend(ongoing_events)
    else:
        new_global_events_parts.append("\n【当前进行中的篇章/事件】\n暂无 (当前为日常过渡)")
        
    new_global_events = "\n".join(new_global_events_parts).strip()
    memory_logger.info(f"📜 当前【防撞梗雷达】更新为：\n{new_global_events}")

    # ==========================================
    # 【可选】事件账本：去重 + 封顶 + 可追溯
    # ==========================================
    # 旧实现（上面那段）把事件表当纯文本，模型不写"事件结束"就永远不收敛：
    # 第 88 章时"进行中"已经堆到 121 条，reviewer 的"只有重演已完结事件才打回"
    # 这条主判据因此形同虚设。开启 features.events_ledger 后改由账本接管。
    events_json = ""
    feats = settings_config.get("features") or {}
    if feats.get("events_ledger", False):
        from agent.canon.events import EventLedger

        ledger = EventLedger.from_text(state.get("events_json") or current_global_events)
        merged = ledger.dedupe()
        tag_result = ledger.apply_tags(clean_tag, state["current_chapter_num"])
        archived = ledger.enforce_budget(
            current_chapter=state["current_chapter_num"],
            max_ongoing=int(settings_config.get("max_ongoing_events", 15) or 15),
        )
        new_global_events = ledger.to_text()
        events_json = ledger.to_json()
        memory_logger.info(
            f"🗂️ 事件账本：合并重复 {merged} 条，开启 {len(tag_result['opened'])} 条，"
            f"结束 {len(tag_result['closed'])} 条，归档 {len(archived)} 条，"
            f"当前 {ledger.stats()}"
        )
        if tag_result["unknown"]:
            memory_logger.warning(f"无法识别的事件指令：{tag_result['unknown']}")

    # ==========================================
    # 更新工作记忆滑动窗口
    # ==========================================
    current_memory = state.get("context_memory", "")
    if current_memory == "无":
        current_memory = ""
        
    new_summary_block = f"【第 {state['current_chapter_num']} 章记忆归档】\n{chapter_summary}\n"
    
    memory_blocks = [m for m in current_memory.split("【第 ") if m.strip()]
    memory_blocks = ["【第 " + m for m in memory_blocks] 
    memory_blocks.append(new_summary_block)
    
    max_memory_chapters = settings_config.get("max_memory_chapters", 5)
    if len(memory_blocks) > max_memory_chapters:
        memory_logger.info(f"工作记忆超过 {max_memory_chapters} 章，正在淘汰最老记忆...")
        memory_blocks = memory_blocks[-max_memory_chapters:]
        
    new_context_memory = "\n".join(memory_blocks)
    
    # ==========================================
    # 存入 ChromaDB 长期记忆库
    # ==========================================
    memory_logger.debug("正在将原文切块并存入 ChromaDB 长期记忆库...")
    embed_model = EmbeddingsFactory().create_model()
    
    chroma_dir_name = chroma_config.get("persist_directory", "memory/chroma_db")
    persist_dir = os.path.join(project_root, chroma_dir_name)
    os.makedirs(persist_dir, exist_ok=True)
    
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    docs = text_splitter.create_documents(
        texts=[draft],
        metadatas=[{"chapter": state['current_chapter_num']}]
    )
    
    try:
        vectorstore = Chroma.from_documents(
            documents=docs,
            embedding=embed_model,
            persist_directory=persist_dir,
            collection_name=chroma_config.get("collection_name", "novel_memory")
        )
        memory_logger.info(f"长期记忆入库成功！本章共切分为 {len(docs)} 个文本块。")
    except Exception as e:
        memory_logger.error(f"存入 ChromaDB 时发生严重错误: {str(e)}")

    memory_logger.info("记忆归档处理完毕，准备进入下一章节。")
    
    return {
        "context_memory": new_context_memory,
        "global_events": new_global_events,
        "events_json": events_json,
        "feedback": "",
        "revision_count": 0
    }
# -------------------------
# 3. 单步运行测试 (独立跑通验证)
# -------------------------
if __name__ == "__main__":
    test_draft = """
    叶凡在小镇的拍卖会上，用尽了所有的灵石，拍下了一截枯木。所有人都嘲笑他是冤大头。
    然而回到客栈后，那枚铜戒指突然散发出光芒，枯木竟然在光芒的照耀下抽出了新芽，
    化作了一把晶莹剔透的木剑。就在此时，门外传来了镇长李霸天的怒喝声：“叶凡，交出宝物，饶你不死！”
    """
    
    test_state: AgentState = {
        "outline": "修仙小说大纲",
        "current_chapter_num": 3,
        "context_memory": "【第 2 章摘要】\n叶凡来到了小镇，发现这里即将举行一场拍卖会。\n",
        "draft_content": test_draft,
        "feedback": "",
        "revision_count": 0
    }
    
    try:
        new_state = memory_node(test_state)
        print("\n" + "="*40 + " 记忆更新结果 " + "="*40)
        print("最新工作记忆：")
        print(new_state["context_memory"])
        print("="*94)
    except Exception as e:
        memory_logger.error(f"测试失败: {e}")