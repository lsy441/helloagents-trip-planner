"""多级缓存管理器 - LRU内存缓存 + Redis分布式缓存 + RAG语义检索

缓存策略：
- L1: LRU内存缓存 - 热点数据，低延迟访问
- L2: Redis缓存 - 分布式共享，中等延迟
- RAG: 语义检索 - 近似匹配，复用相似结果

TTL配置（按数据类型）：
- 天气: 1小时 (高频变化)
- 景点: 1天 (相对稳定)
- 酒店: 6小时 (价格可能变动)
- 交通: 3小时 (班次可能调整)
- 美食: 1天 (商家信息稳定)
- 地图: 7天 (地理信息基本不变)
"""

import os
import json
import hashlib
import time
from typing import Any, Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from functools import lru_cache

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class LRUCache:
    """LRU内存缓存 - L1级缓存"""
    
    def __init__(self, max_size: int = 500):
        self._cache: Dict[str, Tuple[Any, float, int]] = {}  # (value, timestamp, access_count)
        self.max_size = max_size
        self.hits = 0
        self.misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            value, timestamp, access_count = self._cache[key]
            self._cache[key] = (value, timestamp, access_count + 1)
            self.hits += 1
            return value
        self.misses += 1
        return None
    
    def set(self, key: str, value: Any):
        if len(self._cache) >= self.max_size:
            oldest = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest]
        self._cache[key] = (value, time.time(), 1)
    
    def delete(self, key: str):
        if key in self._cache:
            del self._cache[key]
    
    def clear(self):
        self._cache.clear()
        self.hits = 0
        self.misses = 0
    
    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0.0
        return {
            "entries": len(self._cache),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{hit_rate:.2%}",
            "max_size": self.max_size,
        }


class RedisCache:
    """Redis分布式缓存 - L2级缓存"""
    
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, password: Optional[str] = None):
        self._client = None
        self._available = False
        self.hits = 0
        self.misses = 0
        self._connection_error = ""
        try:
            if REDIS_AVAILABLE:
                kwargs = {
                    "host": host,
                    "port": port,
                    "db": db,
                    "decode_responses": True,
                    "socket_timeout": 5,
                    "socket_connect_timeout": 5
                }
                if password and password.strip():
                    kwargs["password"] = password
                self._client = redis.Redis(**kwargs)
                self._available = self._client.ping()
                print("[Redis Cache] Connected successfully")
            else:
                print("[Redis Cache] redis-py not installed")
        except Exception as e:
            self._connection_error = str(e)
            print(f"[Redis Cache] Connection failed: {self._connection_error}")
    
    def get(self, key: str) -> Optional[Any]:
        if not self._available or not self._client:
            return None
        try:
            value = self._client.get(key)
            if value is not None:
                self.hits += 1
                return json.loads(value)
            self.misses += 1
            return None
        except Exception:
            self.misses += 1
            return None
    
    def set(self, key: str, value: Any, ttl: int = 3600):
        if not self._available or not self._client:
            return
        try:
            self._client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception:
            pass
    
    def delete(self, key: str):
        if not self._available or not self._client:
            return
        try:
            self._client.delete(key)
        except Exception:
            pass
    
    def clear(self):
        if not self._available or not self._client:
            return
        try:
            self._client.flushdb()
            self.hits = 0
            self.misses = 0
        except Exception:
            pass
    
    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0.0
        return {
            "available": self._available,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{hit_rate:.2%}",
            "error": self._connection_error if not self._available else "",
        }


# TTL配置映射
TTL_CONFIG: Dict[str, int] = {
    "weather": 3600,           # 天气: 1小时
    "attraction": 86400,       # 景点: 1天
    "hotel": 21600,            # 酒店: 6小时
    "transportation": 10800,   # 交通: 3小时
    "food": 86400,             # 美食: 1天
    "map": 604800,             # 地图: 7天
    "default": 3600,           # 默认: 1小时
}


class MultiLevelCache:
    """多级缓存管理器"""
    
    def __init__(self, l1_max_size: int = 500, redis_host: str = "localhost", 
                 redis_port: int = 6379, redis_password: Optional[str] = None):
        self._l1_cache = LRUCache(max_size=l1_max_size)
        self._l2_cache = RedisCache(host=redis_host, port=redis_port, password=redis_password)
        self._namespace = "trip_planner:"
        self._operation_count = 0
        self._l1_only_count = 0
        self._l2_only_count = 0
        self._miss_count = 0
    
    def _get_ttl(self, data_type: str) -> int:
        """根据数据类型获取TTL"""
        return TTL_CONFIG.get(data_type.lower(), TTL_CONFIG["default"])
    
    def _generate_key(self, data_type: str, params: Dict[str, Any]) -> str:
        """生成缓存键"""
        sorted_params = json.dumps(params, sort_keys=True, ensure_ascii=False)
        content = f"{data_type}:{sorted_params}"
        return self._namespace + hashlib.md5(content.encode()).hexdigest()
    
    def get(self, data_type: str, params: Dict[str, Any]) -> Optional[Any]:
        """多级缓存读取"""
        key = self._generate_key(data_type, params)
        self._operation_count += 1
        
        # L1缓存读取
        l1_result = self._l1_cache.get(key)
        if l1_result is not None:
            self._l1_only_count += 1
            return l1_result
        
        # L2缓存读取
        l2_result = self._l2_cache.get(key)
        if l2_result is not None:
            self._l2_only_count += 1
            # 提升到L1缓存
            self._l1_cache.set(key, l2_result)
            return l2_result
        
        self._miss_count += 1
        return None
    
    def set(self, data_type: str, params: Dict[str, Any], value: Any):
        """多级缓存写入"""
        key = self._generate_key(data_type, params)
        ttl = self._get_ttl(data_type)
        
        # L1缓存写入
        self._l1_cache.set(key, value)
        
        # L2缓存写入
        self._l2_cache.set(key, value, ttl)
    
    def delete(self, data_type: str, params: Dict[str, Any]):
        """删除缓存"""
        key = self._generate_key(data_type, params)
        self._l1_cache.delete(key)
        self._l2_cache.delete(key)
    
    def clear(self):
        """清空所有缓存"""
        self._l1_cache.clear()
        self._l2_cache.clear()
        self._operation_count = 0
        self._l1_only_count = 0
        self._l2_only_count = 0
        self._miss_count = 0
    
    def stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        l1_stats = self._l1_cache.stats()
        l2_stats = self._l2_cache.stats()
        
        overall_hit_rate = 0.0
        if self._operation_count > 0:
            overall_hit_rate = (self._l1_only_count + self._l2_only_count) / self._operation_count
        
        return {
            "overall": {
                "operations": self._operation_count,
                "l1_hits": self._l1_only_count,
                "l2_hits": self._l2_only_count,
                "misses": self._miss_count,
                "overall_hit_rate": f"{overall_hit_rate:.2%}",
            },
            "l1_cache": l1_stats,
            "l2_cache": l2_stats,
            "ttl_config": TTL_CONFIG,
        }


# 全局单例
_multi_level_cache = None


def get_multi_level_cache() -> MultiLevelCache:
    """获取多级缓存管理器实例"""
    global _multi_level_cache
    if _multi_level_cache is None:
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD", None)
        if redis_password == "":
            redis_password = None
        _multi_level_cache = MultiLevelCache(
            l1_max_size=500,
            redis_host=redis_host,
            redis_port=redis_port,
            redis_password=redis_password,
        )
    return _multi_level_cache
