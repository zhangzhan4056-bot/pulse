"""基础获取器 - 统一的错误处理、重试、限流"""

import time
import logging
from functools import wraps

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


def rate_limit(calls_per_minute: int):
    """速率限制装饰器"""
    min_interval = 60.0 / calls_per_minute
    last_call = [0.0]

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_call[0]
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            result = func(*args, **kwargs)
            last_call[0] = time.time()
            return result
        return wrapper
    return decorator


def create_session(retries: int = 5, backoff_factor: float = 2.0) -> requests.Session:
    """创建带重试机制的 requests session"""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def check_response(data: dict, context: str = "") -> None:
    """检查 API 响应中的错误

    Twelve Data 有时返回 HTTP 200 但 body 中包含错误
    """
    if isinstance(data, dict) and data.get("status") == "error":
        code = data.get("code", "unknown")
        message = data.get("message", "未知错误")
        raise ValueError(f"API 错误 [{context}]: {code} - {message}")


class BaseFetcher:
    """基础数据获取器"""

    def __init__(self):
        self.session = create_session()
