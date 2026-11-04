from abc import ABC, abstractmethod
from typing import Optional, Union
import os

from langchain_core.embeddings import Embeddings
# 【修改1】修复了原来从 tongyi 导入 BaseChatModel 的错误，改为从核心层导入
from langchain_core.language_models.chat_models import BaseChatModel 
from langchain_ollama import OllamaEmbeddings
from langchain_openai import ChatOpenAI

# 【修改2】假设这里适配你之前定义的配置模块，从 agent.yml 读取配置
from utils.config_handler import agent_config 

class BaseModelFactory(ABC):
    @abstractmethod
    def create_model(self, **kwargs) -> Union[Embeddings, BaseChatModel]:
        """
        创建并返回模型实例
        使用 **kwargs 允许子类接收不同的实例化参数
        """
        pass

class ChatModelFactory(BaseModelFactory):
    
    def create_model(self, model_name: Optional[str] = None, temperature: float = 0.7) -> BaseChatModel:
        """
        创建对话模型（大模型）
        【修改3】增加了 model_name 和 temperature 参数。
        为什么加 temperature？
        - 写手Agent 需要创造力，实例化时可以传 temperature=0.8
        - 审稿Agent 需要严谨和逻辑，实例化时可以传 temperature=0.1
        - 记忆Agent 需要客观总结，实例化时可以传 temperature=0.3
        """
        # 获取 API Key，优先从配置文件获取，如果没有则尝试从环境变量获取
        api_key = agent_config.get("volc_api_key") or os.environ.get("ARK_API_KEY")
        
        if not api_key:
            raise ValueError("未找到火山引擎 API Key，请在 config/agent.yml 中配置 volc_api_key 或设置系统环境变量")

        # 允许代码层传入具体的 endpoint_id，如果不传则默认使用配置文件中的
        actual_model_name = model_name or agent_config.get("chat_model_name")

        # 火山引擎的 Ark v3 接口完全兼容 OpenAI 的 SDK
        return ChatOpenAI(
            model=actual_model_name, 
            api_key=api_key,
            base_url="https://ark.cn-beijing.volces.com/api/v3", 
            max_retries=3,
            temperature=temperature # 动态温度
        )


class EmbeddingsFactory(BaseModelFactory):
    
    def create_model(self) -> Embeddings:
        """
        创建词嵌入模型，用于记忆Agent的上下文压缩与检索
        """
        # 这里建议也从 config 中读取你的 Ollama base_url (如果不是默认的 localhost:11434)
        ollama_base_url = agent_config.get("ollama_base_url", "http://localhost:11434")
        
        return OllamaEmbeddings(
            model=agent_config.get("embedding_model_name", "nomic-embed-text"), # 提供一个默认的常用向量模型名
            base_url=ollama_base_url
        )


# 【修改4】这里不建议直接在模块底部实例化唯一的全局单例。
# 更好的做法是在具体使用 Agent 的地方（例如定义 LangGraph 节点时）再去实例化，
# 这样可以根据 Agent 的角色传入不同的参数。

# 下面提供几个常用预设，可以在其他文件中直接 import 使用：
# from model.factory import writer_llm, reviewer_llm, memory_llm

writer_llm = ChatModelFactory().create_model(temperature=0.85)  # 创造力拉满的写手模型
reviewer_llm = ChatModelFactory().create_model(temperature=0.1) # 冷酷无情的审稿模型
memory_llm = ChatModelFactory().create_model(temperature=0.3)   # 客观理智的记忆模型
embedding_model = EmbeddingsFactory().create_model()            # 向量模型