import functools
import inspect
import logging
import time
import traceback
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ParamSpec, TypeVar, overload

import colorlog
from openai import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ContentFilterFinishReasonError,
    InternalServerError,
    InvalidWebhookSignatureError,
    LengthFinishReasonError,
    NotFoundError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from pydantic import ValidationError

from deeppresenter.utils.constants import LOGGING_LEVEL

_context_logger: ContextVar[logging.Logger | None] = ContextVar(
    "_context_logger", default=None
)
P = ParamSpec("P")
R = TypeVar("R")


def create_logger(
    name: str = __name__, log_file: str | Path | None = None
) -> logging.Logger:
    """Create a new logger"""
    assert name == "default logger" or name not in logging.Logger.manager.loggerDict, (
        f"Logger '{name}' already exists."
    )
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Clear existing handlers to avoid duplicates
    logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(LOGGING_LEVEL)
    formatter = colorlog.ColoredFormatter(
        "%(log_color)s%(levelname)-4s%(reset)s %(asctime)s [%(name)s] %(blue)s%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        reset=True,
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red,bg_white",
        },
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(levelname)-4s %(asctime)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def set_logger(name: str = __name__, log_file: str | Path | None = None):
    """Set a new logger for the current async context"""
    logger = _context_logger.get()
    assert logger is None or logger.name == "default logger", (
        "Context logger is already set."
    )
    logger = create_logger(name, log_file)
    logger.debug("Setting new context logger with loglevel=%s", LOGGING_LEVEL)
    _context_logger.set(logger)
    return logger


def get_logger() -> logging.Logger:
    """Get the logger for the current context"""

    ctx_logger = _context_logger.get()
    if ctx_logger is None:
        ctx_logger = create_logger("default logger")
        _context_logger.set(ctx_logger)
    return ctx_logger


def debug(msg, *args, **kwargs):
    get_logger().debug(msg, *args, **kwargs)


def info(msg, *args, **kwargs):
    get_logger().info(msg, *args, **kwargs)


def warning(msg, *args, **kwargs):
    get_logger().warning(msg, *args, **kwargs)


def error(msg, *args, **kwargs):
    get_logger().error(msg, *args, **kwargs)


def critical(msg, *args, **kwargs):
    get_logger().critical(msg, *args, **kwargs)


def exception(msg, *args, **kwargs):
    get_logger().exception(msg, *args, **kwargs)


class timer:
    """Timer context manager and decorator"""

    def __init__(self, name: str = None):
        self.name = name
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        elapsed = time.time() - self.start_time
        if elapsed > 1:
            debug(f"{self.name} took {elapsed:.2f} seconds")

    @overload
    def __call__(
        self, func: Callable[P, Coroutine[Any, Any, R]]
    ) -> Callable[P, Coroutine[Any, Any, R]]: ...

    @overload
    def __call__(self, func: Callable[P, R]) -> Callable[P, R]: ...

    def __call__(self, func):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start = time.time()
                try:
                    return await func(*args, **kwargs)
                finally:
                    elapsed = time.time() - start
                    if elapsed > 1:
                        debug(
                            f"{self.name or func.__name__} took {elapsed:.2f} seconds"
                        )

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                start = time.time()
                try:
                    return func(*args, **kwargs)
                finally:
                    elapsed = time.time() - start
                    if elapsed > 1:
                        debug(
                            f"{self.name or func.__name__} took {elapsed:.2f} seconds"
                        )

            return sync_wrapper


def logging_openai_exceptions(identifider: str | Any, exc: Exception):
    """记录 OpenAI 异常"""
    if isinstance(exc, RateLimitError):
        msg = f"速率限制错误 (HTTP 429): {str(exc)}"
    elif isinstance(exc, APITimeoutError):
        msg = f"API 超时错误: {str(exc)}"
    elif isinstance(exc, APIConnectionError):
        msg = f"API 连接错误: {str(exc)}"
    elif isinstance(exc, AuthenticationError):
        msg = f"认证错误 (HTTP 401): {str(exc)}"
    elif isinstance(exc, PermissionDeniedError):
        msg = f"权限拒绝错误 (HTTP 403): {str(exc)}"
    elif isinstance(exc, NotFoundError):
        msg = f"未找到错误 (HTTP 404): {str(exc)}"
    elif isinstance(exc, ConflictError):
        msg = f"冲突错误 (HTTP 409): {str(exc)}"
    elif isinstance(exc, BadRequestError):
        msg = f"错误请求 (HTTP 400): {str(exc)}"
    elif isinstance(exc, UnprocessableEntityError):
        msg = f"无法处理实体错误 (HTTP 422): {str(exc)}"
    elif isinstance(exc, InternalServerError):
        msg = f"内部服务器错误 (HTTP 500): {str(exc)}"
    elif isinstance(exc, APIStatusError):
        code = getattr(exc, "status_code", "unknown")
        msg = f"API 状态错误 (HTTP {code}): {str(exc)}"
    elif isinstance(exc, APIError):
        msg = f"API 错误: {str(exc)}"
    elif isinstance(exc, APIResponseValidationError):
        msg = f"API 响应验证错误: {str(exc)}"
    elif isinstance(exc, InvalidWebhookSignatureError):
        msg = f"无效 Webhook 签名错误: {str(exc)}"
    elif isinstance(exc, ContentFilterFinishReasonError):
        msg = f"内容过滤完成原因错误: {str(exc)}"
    elif isinstance(exc, LengthFinishReasonError):
        msg = f"长度完成原因错误: {str(exc)}"
    elif isinstance(exc, OpenAIError):
        msg = f"OpenAI 错误: {str(exc)}"
    elif isinstance(exc, ValidationError):
        msg = f"Pydantic 验证错误: {str(exc)}"
    elif hasattr(exc, "http_status"):
        msg = f"OpenAI API 错误 {exc.http_status}: {str(exc)}"
    else:
        msg = f"异常: {str(exc)}\n{traceback.format_exc()}"

    warning(f"{identifider} 遇到 {msg}")
    return msg
