import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def decode_bytes_autodetect(data):
    """解码字节，自动探测编码：先按 UTF-8 严格解码，失败回退 GBK，最后兜底 replace。

    打包版子进程 stdout 被重定向到日志文件时默认按系统 ANSI 编码（中文 Windows 为
    GBK）输出，旧日志与部分新日志是 GBK；app.log（FileHandler 指定 utf-8）为 UTF-8。
    两种编码混存时统一用本函数解码即可正确显示中文。
    """
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_text_autodetect(path):
    """读取文本文件，自动探测编码（UTF-8 严格 → GBK 回退 → replace 兜底）。"""
    return decode_bytes_autodetect(Path(path).read_bytes())

# 日志格式
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"


def _app_log_path():
    """应用日志路径（惰性解析，避免 import 期基于 cwd 的副作用）。"""
    try:
        from utils.config import data_dir
    except ImportError:
        # utils.config 尚未完成初始化（循环导入窗口期），退回 cwd 相对路径
        return Path("app.log")
    root = data_dir()
    try:
        (root / "logs").mkdir(parents=True, exist_ok=True)
        return root / "logs" / "app.log"
    except OSError:
        # 数据目录不可写时退回 cwd 相对路径，尽力保证日志可用
        return Path("app.log")


# 配置日志
def setup_logger(name="app", level=logging.INFO):
    """
    配置日志记录器
    :param name: 日志记录器名称
    :param level: 日志级别
    :return: 配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 防止重复添加处理器
    if not logger.handlers:
        # 控制台日志处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(LOG_FORMAT)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # 文件日志处理器（带日志轮转）；目标目录不可写时只保留控制台输出，
        # 绝不让日志初始化失败拖垮应用启动
        try:
            file_handler = RotatingFileHandler(_app_log_path(), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            file_handler.setLevel(level)
            file_formatter = logging.Formatter(LOG_FORMAT)
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        except OSError:
            pass

    return logger


# 示例：使用日志记录器
if __name__ == "__main__":
    logger = setup_logger(level=logging.DEBUG)
    logger.debug("这是一个调试信息")
    logger.info("这是一个普通信息")
    logger.warning("这是一个警告信息")
    logger.error("这是一个错误信息")
    logger.critical("这是一个严重错误信息")