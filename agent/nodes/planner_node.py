import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langchain_core.prompts import PromptTemplate
from langchain_core.messages import HumanMessage
from model.factory import ChatModelFactory
from utils.logger_handler import get_logger
from utils.prompt_loader import load_prompt
from agent.state import AgentState

planner_logger = get_logger("Planner")

def planner_node(state: AgentState) -> dict:
    planner_logger.info(f"=== 开始规划 第 {state['current_chapter_num']} 章细纲 ===")
    
    if state.get("feedback") and state.get("chapter_plan"):
        planner_logger.info("当前为重写阶段，沿用原有细纲。")
        return {}

    llm = ChatModelFactory().create_model(temperature=0.5)
    prompt_text = load_prompt("planner_prompt_path")

    base_outline = state.get("outline", "")
    director_notes = state.get("director_notes", "").strip()
    if director_notes:
        base_outline += f"\n\n【场外导演临时指导/灵感】\n{director_notes}"

    prompt_template = PromptTemplate(
        input_variables=["outline", "current_chapter_num", "context_memory", "previous_tail", "global_events"], # 【新增变量】
        template=prompt_text
    )
    
    formatted_prompt = prompt_template.format(
        outline=base_outline,
        current_chapter_num=state.get("current_chapter_num", 1),
        context_memory=state.get("context_memory", "无"),
        previous_tail=state.get("previous_tail", "无"),
        global_events=state.get("global_events", "无") # 【注入年表】
    )

    messages = [HumanMessage(content=formatted_prompt)]
    response = llm.invoke(messages)
    
    return {"chapter_plan": response.content}

if __name__ == "__main__":
    # 独立运行测试
    test_state: AgentState = {
        "outline": "这是一部修仙群像剧，主角叶凡，配角有庞博、李小曼、黑皇等。第一阶段叶凡在小镇觉醒，第二阶段去灵墟洞天。",
        "director_notes": "",
        "current_chapter_num": 1,
        "context_memory": "无",
        "chapter_plan": "",
        "draft_content": "",
        "feedback": "",
        "revision_count": 0
    }

    try:
        new_state = planner_node(test_state)
        print("\n" + "="*40 + " 编剧规划结果 " + "="*40)
        print(new_state["chapter_plan"])
        print("="*94)
    except Exception as e:
        planner_logger.error(f"规划节点运行出错: {str(e)}")