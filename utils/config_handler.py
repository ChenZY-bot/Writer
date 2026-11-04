"""
yaml 配置文件解析工具
读取 config 文件夹下的核心配置
"""
import os
import sys
import yaml

# 自动获取项目根目录并将其加入系统路径，解决直接运行测试时的包导入问题
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.logger_handler import sys_logger


config_dir = os.path.join(project_root, "config")

def load_yaml_config(filename: str, encoding: str = "utf-8") -> dict:
    """
    通用的 yaml 配置文件加载函数
    """
    config_path = os.path.join(config_dir, filename)
    try:
        with open(config_path, "r", encoding=encoding) as f:
            # 使用 safe_load 解析更安全
            config = yaml.safe_load(f)
            return config or {}
    except FileNotFoundError:
        sys_logger.warning(f"[config_handler] 未找到配置文件: {config_path}，将返回空字典")
        return {}
    except Exception as e:
        sys_logger.error(f"[config_handler] 读取配置文件 {filename} 出错: {str(e)}")
        return {}

# ==========================================
# 预加载所有配置项并暴露出字典，供全局引入使用
# 例如: from utils.config_handler import prompt_config
# ==========================================
agent_config = load_yaml_config("agent_config.yml")
chroma_config = load_yaml_config("chroma_config.yml")
prompt_config = load_yaml_config("prompt_config.yml")
settings_config = load_yaml_config("settings_config.yml")

if __name__ == '__main__':
    # 本地跑通测试
    print("Agent Config 测试读取:", agent_config)
    print("Prompt Config 测试读取:", prompt_config)