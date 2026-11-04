import os
import sys
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import HumanMessage

# 获取项目根目录，方便导入
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

# 导入基础设施
from model.factory import ChatModelFactory
from utils.logger_handler import writer_logger
from utils.prompt_loader import load_prompt
from agent.tools.retriever_tools import retrieve_long_term_memory
from agent.state import AgentState

# -------------------------
# 2. 节点核心逻辑
# -------------------------
def writer_node(state: AgentState) -> dict:
    """
    写手Agent节点执行函数
    """
    writer_logger.info(f"=== 开始撰写 第 {state['current_chapter_num']} 章 ===")
    
    # 检查是否是重写阶段
    if state.get("feedback") and state["feedback"].strip():
        writer_logger.warning(f"检测到审稿反馈，正在进行第 {state.get('revision_count', 0) + 1} 次重写...")
    else:
        writer_logger.info("首次起草本章内容...")

    # ==========================================
    # 【新增：处理场外导演的临时灵感】
    # ==========================================
    base_outline = state.get("outline", "")
    director_notes = state.get("director_notes", "").strip()
    
    # 如果有临时灵感，直接动态拼接到大纲末尾，这样就不需要修改原有的 prompt 文件了
    if director_notes:
        writer_logger.info(f"🎬 接收到场外导演临时指导：{director_notes}")
        base_outline += f"\n\n【场外导演临时指导/灵感】\n{director_notes}\n(请在撰写本章时，自然地融入上述灵感要求，不要显得生硬)"

    # ==========================================
    # 【新增：终极双轨记忆融合】调用检索工具获取长期记忆
    # ==========================================
    writer_logger.debug("正在查询长期记忆(跨章节伏笔)...")
    # 【修改】：不再用宽泛的 base_outline，而是用精准的 chapter_plan 去向量库里捞设定
    chapter_plan = state.get("chapter_plan", "")
    long_term_memory = retrieve_long_term_memory(query=chapter_plan)

    # 1. 实例化写手大模型 (创造力较高，temperature设为0.85)
    writer_logger.debug("正在加载大模型实例...")
    llm = ChatModelFactory().create_model(temperature=0.85)

    # 2. 读取 Prompt 模板
    prompt_text = load_prompt("writer_prompt_path")

    # 3. 组装并格式化 Prompt
    prompt_template = PromptTemplate(
        input_variables=["outline", "chapter_plan", "current_chapter_num", "context_memory", "long_term_memory", "feedback", "previous_tail"],
        template=prompt_text
    )
    
    formatted_prompt = prompt_template.format(
        outline=base_outline,
        chapter_plan=chapter_plan,
        current_chapter_num=state.get("current_chapter_num", 1),
        context_memory=state.get("context_memory", "无（这是小说的开头）。"),
        long_term_memory=long_term_memory, #注入长期记忆
        feedback=state.get("feedback", "无"),
        previous_tail=state.get("previous_tail", "无（这是第一章，暂无前文）。") # 注入数据
    )

    # 4. 调用大模型生成内容
    writer_logger.info("正在奋笔疾书中，这可能需要几十秒，请稍候...")
    
    # 将格式化后的文本转为 Message 传递给 Chat 模型
    messages = [HumanMessage(content=formatted_prompt)]
    response = llm.invoke(messages)
    
    # 检查生成内容是否为空
    if not response.content.strip():
        writer_logger.error("大模型生成了空内容！请检查模型配置或提示模板。")
        raise ValueError("生成的章节内容为空")

    # 从模型响应中提取章节内容
    chapter_content = response.content
    writer_logger.info(f"第 {state['current_chapter_num']} 章撰写完成！共计 {len(chapter_content)} 字。")

    # 5. 返回更新后的状态
    # 提示：在 LangGraph 中，返回的字典会自动覆盖/追加到全局 State 中
    return {
        "draft_content": chapter_content,
    }


# -------------------------
# 3. 单步运行测试 (独立跑通验证)
# -------------------------
if __name__ == "__main__":
    # 模拟用户输入的大纲和初始状态
    test_state: AgentState = {
        "outline": "叶凡决定离开小镇，前往更广阔的天地。他拿出了之前得到的木剑，研究其中的奥秘，准备御剑飞行。",
        "director_notes": "在本章结尾处，让天空突然下起红色的血雨，并出现一个撑着红伞的神秘少女。",
        "current_chapter_num": 4,
        "context_memory": "【第 3 章摘要】\n叶凡击败了李霸天，震惊了所有人。\n",  # 第一章没有历史记忆
        "chapter_plan": "本章核心：叶凡研究木剑并离开小镇，路遇异象与红伞少女。【准许出场人物】：叶凡、红伞少女。其他配角严禁出场。",
        "feedback": "",        # 第一次写没有审稿意见
        "draft_content": "",
        "revision_count": 0
    }

    try:
        # 运行节点
        new_state_update = writer_node(test_state)
        
        # 打印生成的文章（截取前500字预览）
        print("\n" + "="*50 + " 生成正文预览 " + "="*50)
        draft = new_state_update['draft_content']
        print(draft[:500] + "......\n(后续字数省略)")
        print("="*114)
        
    except Exception as e:
        writer_logger.error(f"写手节点运行出错: {str(e)}", exc_info=True)