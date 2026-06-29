"""可观测性模块 - loguru日志 + @timer耗时装饰器 + MetricsCollector统计"""

import time
import functools
import sys
from typing import Dict, Optional, Callable, Any
from datetime import datetime

from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    colorize=True,
)
logger.add(
    "logs/app_{time:YYYY-MM-DD}.log",
    rotation="10 MB",
    retention="7 days",
    level="DEBUG",
    encoding="utf-8",
)


class MetricsCollector:
    """简单的指标统计类"""

    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._timers: Dict[str, list] = {}
        self._start_time = datetime.now()

    def increment(self, name: str, value: int = 1):
        self._counters[name] = self._counters.get(name, 0) + value

    def record_time(self, name: str, seconds: float):
        if name not in self._timers:
            self._timers[name] = []
        self._timers[name].append(seconds)

    def get_counters(self) -> Dict[str, int]:
        return dict(self._counters)

    def get_timers(self) -> Dict[str, dict]:
        result = {}
        for name, times in self._timers.items():
            if not times:
                continue
            result[name] = {
                "count": len(times),
                "total": round(sum(times), 3),
                "avg": round(sum(times) / len(times), 3),
                "min": round(min(times), 3),
                "max": round(max(times), 3),
            }
        return result

    def get_summary(self) -> dict:
        uptime = (datetime.now() - self._start_time).total_seconds()
        return {
            "uptime_seconds": round(uptime, 1),
            "uptime_human": f"{int(uptime // 3600)}h{int((uptime % 3600) // 60)}m{int(uptime % 60)}s",
            "counters": self.get_counters(),
            "timers": self.get_timers(),
        }

    def reset(self):
        self._counters.clear()
        self._timers.clear()
        self._start_time = datetime.now()


_metrics = MetricsCollector()


def get_metrics_collector() -> MetricsCollector:
    return _metrics


def timer(name: Optional[str] = None):
    """耗时装饰器 - 记录函数执行时间并输出日志"""
    def decorator(func: Callable) -> Callable:
        timer_name = name or f"{func.__module__}.{func.__name__}"

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            metrics = get_metrics_collector()
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                metrics.record_time(timer_name, elapsed)
                logger.info(f"[{timer_name}] 耗时 {elapsed:.3f}s")
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                metrics.record_time(timer_name, elapsed)
                logger.error(f"[{timer_name}] 失败 ({elapsed:.3f}s): {e}")
                raise

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            metrics = get_metrics_collector()
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                metrics.record_time(timer_name, elapsed)
                logger.info(f"[{timer_name}] 耗时 {elapsed:.3f}s")
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                metrics.record_time(timer_name, elapsed)
                logger.error(f"[{timer_name}] 失败 ({elapsed:.3f}s): {e}")
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
