import os
import sys
import json
import re

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langchain_core.prompts import PromptTemplate
from langchain_core.messages import HumanMessage
from model.factory import ChatModelFactory
from utils.logger_handler import reviewer_logger
from utils.prompt_loader import load_prompt
from agent.state import AgentState

def reviewer_node(state: AgentState) -> dict:
    reviewer_logger.info(f"=== 开始审查 第 {state['current_chapter_num']} 章 ===")
    
    if not state.get("draft_content"):
        return {"feedback": "草稿内容为空，请重新生成。"}

    llm = ChatModelFactory().create_model(temperature=0.1)
    prompt_text = load_prompt("reviewer_prompt_path")

    prompt_text = prompt_text.replace("{", "{{").replace("}", "}}")
    valid_vars = ["outline", "chapter_plan", "context_memory", "previous_tail", "global_events", "current_chapter_num", "draft_content"] # 【新增】
    for var in valid_vars:
        prompt_text = prompt_text.replace(f"{{{{{var}}}}}", f"{{{var}}}")

    prompt_template = PromptTemplate(input_variables=valid_vars, template=prompt_text)
    
    formatted_prompt = prompt_template.format(
        outline=state.get("outline", ""),
        chapter_plan=state.get("chapter_plan", ""),
        context_memory=state.get("context_memory", "无"),
        previous_tail=state.get("previous_tail", "无"),
        global_events=state.get("global_events", "无"), # 【注入年表】
        current_chapter_num=state.get("current_chapter_num", 1),
        draft_content=state.get("draft_content", "")
    )

    messages = [HumanMessage(content=formatted_prompt)]
    try:
        response = llm.invoke(messages)
        raw_output = response.content.strip()
        
        # 强力正则提取 JSON，防止大模型在前面加废话
        match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if match:
            raw_output = match.group(0)
            
        result = json.loads(raw_output)
        
        # 兼容 true/false 是字符串的情况
        is_pass_raw = result.get("pass", False)
        if str(is_pass_raw).lower() == "true":
            is_pass = True
        else:
            is_pass = False
            
        feedback = result.get("feedback", "")
        
    except Exception as e:
        reviewer_logger.error(f"JSON解析失败: {e}\n原文: {response.content}")
        is_pass = False
        feedback = "系统无法解析你的JSON，请严格遵守格式输出！"

    if is_pass:
        reviewer_logger.info(f"✅ 第 {state['current_chapter_num']} 章 审稿通过！逻辑与衔接完美！")
        return {"feedback": ""}
    else:
        reviewer_logger.warning(f"❌ 审稿不通过！被打回。意见: {feedback}")
        return {
            "feedback": feedback,
            "revision_count": state.get("revision_count", 0) + 1
        }