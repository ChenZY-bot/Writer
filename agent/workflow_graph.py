import os
import sys
from typing import TypedDict

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.graph import StateGraph, END
from agent.nodes.planner_node import planner_node
from agent.nodes.writer_node import writer_node
from agent.nodes.gate_node import gate_node
from agent.nodes.reviewer_node import reviewer_node
from agent.nodes.memory_node import memory_node
from agent.state import AgentState  # 唯一真源，五个节点共用
from utils.logger_handler import sys_logger
from utils.config_handler import settings_config

# -------------------------
# 1. 全局状态 (State)
# -------------------------
# 字段定义已收敛到 agent/state.py，避免五个节点各写一份、字段互相漂移。
# 这里保留同名导入，既兼容原有 `from agent.workflow_graph import AgentState`，
# 也让 LangGraph 拿到同一份 schema。

# -------------------------
# 2. 定义条件路由函数 (Router)
# -------------------------
def route_gate_result(state: AgentState) -> str:
    """客观闸门之后的走向：过了去审稿，不过回写手，超限直接结束。"""
    feedback = state.get("feedback", "")
    revision_count = state.get("revision_count", 0)
    max_retries = settings_config.get("max_retries", 3)

    if not feedback:
        return "passed"
    if revision_count >= max_retries:
        sys_logger.error(f"路由判定：闸门达最大重试次数({max_retries})，强制中断！")
        return "max_retries"
    sys_logger.info(f"路由判定：闸门打回写手，第 {revision_count + 1} 次重写。")
    return "failed"


def route_reviewer_result(state: AgentState) -> str:
    feedback = state.get("feedback", "")
    revision_count = state.get("revision_count", 0)
    max_retries = settings_config.get("max_retries", 3)
    
    if not feedback:
        sys_logger.info(f"路由判定：第 {state['current_chapter_num']} 章审稿通过！进入记忆归档。")
        return "passed"
        
    if revision_count >= max_retries:
        sys_logger.error(f"路由判定：达到最大重试次数({max_retries})，强制中断！")
        return "max_retries"
        
    sys_logger.info(f"路由判定：打回给写手进行第 {revision_count + 1} 次重写。")
    return "failed"

# -------------------------
# 3. 构建 LangGraph 完整工业流水线
# -------------------------
sys_logger.info("正在构建 大文豪Agent 完整工作流图...")
workflow = StateGraph(AgentState)

workflow.add_node("Planner", planner_node)
workflow.add_node("Writer", writer_node)
workflow.add_node("Gate", gate_node)
workflow.add_node("Reviewer", reviewer_node)
workflow.add_node("Memory", memory_node)

workflow.set_entry_point("Planner")
workflow.add_edge("Planner", "Writer")
workflow.add_edge("Writer", "Gate")

# 闸门：硬性问题回写手，否则进审稿人
workflow.add_conditional_edges(
    "Gate",
    route_gate_result,
    {
        "passed": "Reviewer",
        "failed": "Writer",
        "max_retries": END,
    }
)

# 【核心修复】将 passed 正确指向 Memory 节点！
workflow.add_conditional_edges(
    "Reviewer",
    route_reviewer_result,
    {
        "passed": "Memory",
        "max_retries": END,
        "failed": "Writer"
    }
)

workflow.add_edge("Memory", END)
app = workflow.compile()

# -------------------------
# 4. 运行测试：见证左右互搏！
# -------------------------
if __name__ == "__main__":
    # 我们故意在初始状态塞入一个“非常苛刻的大纲”，来看看写手第一次能不能写对，
    # 如果写得不够好，审稿人会打回，随后写手会自动根据反馈进行重写！
    initial_state: AgentState = {
        "outline": "这是一部科幻小说。主角在火星基地醒来，发现所有人都消失了，只留下一只机械狗。基调是孤独、悬疑，绝对不能出现魔法或修仙元素。",
        "current_chapter_num": 1,
        "context_memory": "无",
        "draft_content": "",
        "feedback": "",
        "revision_count": 0
    }

    sys_logger.info("========== 启动自动生成循环 ==========")
    
    # 使用 invoke 运行工作流，它会自动阻塞直到走完整个流程，并返回最终状态
    final_state = app.invoke(initial_state)
            
    sys_logger.info("========== 生成循环结束 ==========")
    
    # 1. 在控制台打印最终通过审稿的小说预览
    print("\n" + "="*40 + " 最终生成正文预览 " + "="*40)
    draft = final_state.get('draft_content', '')
    print(draft[:500] + "\n\n...... (后续字数省略)")
    print("="*98)
    
    # 2. 调用文件保存工具，将完整内容持久化到 settings.yml 配置的文件夹中
    # 为了避免相对导入报错，这里动态导入并执行
    sys_logger.info("正在将最终定稿保存到本地...")
    from utils.file_handler import save_chapter
    save_chapter(final_state["current_chapter_num"], draft)