import asyncio
import concurrent.futures
import requests
from utils.config import get_config

hitokotoApi = "https://v1.hitokoto.cn/"

allHitokotoTypes = {
    "动画": "a",
    "漫画": "b",
    "游戏": "c",
    "文学": "d",
    "原创": "e",
    "来自网络": "f",
    "其他": "g",
    "影视": "h",
    "诗词": "i",
    "哲学": "k",
    "抖机灵": "l",
}

# 共享线程池：避免在 asyncio 事件循环线程内做同步 HTTP（会冻结整个循环，
# 导致多账号调度停摆），也避免每条消息新建线程池。
_hitokoto_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="hitokoto",
)


def _request_hitokoto_sync():
    """请求一言 API 获取一句话（同步实现，请在事件循环外调用）。"""
    config = get_config()

    api_url = hitokotoApi

    for t in allHitokotoTypes.keys():
        if t in config["hitokotoTypes"]:
            if "?" not in api_url:
                api_url += "?"
            if "c=" in api_url:
                api_url += f"&c={allHitokotoTypes[t]}"
            else:
                api_url += f"c={allHitokotoTypes[t]}"

    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        theFrom = data.get("from")
        if theFrom is None or theFrom.strip() == "":
            theFrom = "未知来源"
        theFromWho = data.get("from_who")
        if theFromWho is None or theFromWho.strip() == "":
            theFromWho = "未知作者"
        return f"{data['hitokoto']} —— {theFrom} ({theFromWho})"
    except Exception:
        return "[error] 无法获取一言内容"


def request_hitokoto():
    """请求一言 API 获取一句话（事件循环线程内自动切到线程池执行）。"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        try:
            return _hitokoto_pool.submit(_request_hitokoto_sync).result(timeout=15)
        except concurrent.futures.TimeoutError:
            return "[error] 无法获取一言内容"
    return _request_hitokoto_sync()
