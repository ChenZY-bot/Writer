import logging
import os
from datetime import datetime

# 自动获取项目根目录 (基于当前文件在 utils/ 目录下向上推一层)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 日志保存的根目录
log_root = os.path.join(project_root, "logs")

# 确保日志的目录存在
os.makedirs(log_root, exist_ok=True)

# 日志的格式配置：专为多Agent协作优化
# 格式示例：[2023-10-25 14:30:00] [WriterAgent] [INFO] [writer_node.py:45] - 正在生成第3章...
default_log_format = logging.Formatter(
    fmt="[%(asctime)s] [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def get_logger(
    name: str = "System",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    log_file: str = None
) -> logging.Logger:
    """
    获取或创建一个配置好的日志记录器 (Logger)
    
    :param name: 日志记录器名称 (建议传入 Agent 的角色名，如 'Writer', 'Reviewer', 'Memory')
    :param console_level: 控制台输出的最低日志等级
    :param file_level: 写入文件的最低日志等级
    :param log_file: 自定义日志文件路径，若为空则按日期和角色自动生成
    :return: logging.Logger 实例
    """

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # 基础等级设为DEBUG，具体输出由Handler拦截过滤

    # 避免在同一上下文中重复调用导致日志重复打印 (避免多重Handler)
    if logger.handlers:
        return logger

    # 1. 创建控制台 Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(default_log_format)
    logger.addHandler(console_handler)

    # 2. 创建文件 Handler
    if not log_file:
        # 默认按天、按模块/Agent隔离日志，如 logs/Writer_20231025.log
        date_str = datetime.now().strftime('%Y%m%d')
        log_file = os.path.join(log_root, f"{name}_{date_str}.log")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(default_log_format)
    logger.addHandler(file_handler)

    return logger

# 提供预设的便捷日志记录器实例，方便在其它模块中直接导入使用
# 例如：from utils.logger_handler import sys_logger, writer_logger
sys_logger = get_logger(name="System")
writer_logger = get_logger(name="Writer")
reviewer_logger = get_logger(name="Reviewer")
memory_logger = get_logger(name="Memory")


if __name__ == "__main__":
    # 测试代码：模拟多Agent工作流中的日志打印
    sys_logger.info("大文豪系统开始初始化...")
    
    writer_logger.debug("正在读取大纲...")
    writer_logger.info("第1章草稿生成完毕，共计2543字。")
    
    reviewer_logger.warning("发现第1章与大纲设定偏离：主角名字拼写错误！")
    
    writer_logger.info("已根据审稿意见完成第1章修正。")
    
    memory_logger.info("正在将第1章内容压缩并存入 Chroma 向量库...")