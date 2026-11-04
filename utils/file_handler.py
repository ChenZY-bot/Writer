import os
import sys

# 自动获取项目根目录并将其加入系统路径，解决单点运行时的导入问题
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.logger_handler import sys_logger
from utils.config_handler import settings_config

def get_output_dir() -> str:
    """解析并创建章节输出目录（兼容绝对/相对路径）。"""
    save_dir_name = settings_config.get("output_dir", "output")
    if os.path.isabs(save_dir_name):
        output_dir = save_dir_name
    else:
        output_dir = os.path.join(project_root, save_dir_name)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def save_chapter(chapter_num: int, content: str) -> str:
    """
    将生成的章节保存到本地文本文件中
    
    :param chapter_num: 章节号
    :param content: 章节正文内容
    :return: 保存的文件绝对路径
    """
    output_dir = get_output_dir()

    
    # 格式化文件名，例如 chapter_001.txt
    filename = f"chapter_{chapter_num:03d}.txt"
    filepath = os.path.join(output_dir, filename)
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            # 可以在文件开头加上章节标题
            f.write(f"第 {chapter_num} 章\n\n")
            f.write(content)
            
        sys_logger.info(f"[file_handler] ✅ 第 {chapter_num} 章已成功保存至: {filepath}")
        return filepath
        
    except Exception as e:
        sys_logger.error(f"[file_handler] ❌ 保存第 {chapter_num} 章失败: {str(e)}")
        raise e

if __name__ == "__main__":
    # 测试文件保存功能
    test_path = save_chapter(1, "这是测试生成的小说正文内容。")
    print(f"测试完成，请检查 {test_path} 是否存在。")