import os
import sys

# 自动获取项目根目录并将其加入系统路径，解决直接运行本文件时出现的包导入(ImportError)问题
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from utils.logger_handler import sys_logger
from utils.config_handler import prompt_config


prompt_dir = os.path.join(project_root, "prompt")

def load_prompt(prompt_key: str) -> str:
    """
    结合 config_handler 的通用提示词加载函数
    
    :param prompt_key: prompt.yml 中的键名，例如 'writer_prompt'
    :return: 提示词文本字符串
    """
    # 1. 首先通过键名去 prompt_config 获取对应的 txt 文件名
    prompt_filename = prompt_config.get(prompt_key)
    
    if not prompt_filename:
        sys_logger.error(f"[load_prompt] 在 prompt.yml 中未配置 key: {prompt_key}")
        raise KeyError(f"Missing configuration for prompt key: {prompt_key}")

    prompt_path = os.path.join(prompt_dir, prompt_filename)
    
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError as e:
        sys_logger.error(f"[load_prompt] 找不到提示词文件: {prompt_path}")
        raise e
    except Exception as e:
        sys_logger.error(f"[load_prompt] 读取提示词文件 {prompt_filename} 时出错: {str(e)}")
        raise e

if __name__ == '__main__':
    # 测试读取写手提示词 (测试前需确保 config/prompt.yml 中包含 writer_prompt: "writer_prompt.txt")
    try:
        content = load_prompt("writer_prompt_path")
        print(f"成功加载提示词，长度: {len(content)} 字符")
    except Exception as e:
        print(f"测试失败: {e}")