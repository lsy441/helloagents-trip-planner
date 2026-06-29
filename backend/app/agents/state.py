"""状态定义和缓存系统"""

import json
import hashlib
from typing import TypedDict, Annotated, List, Dict, Any, Optional, Literal
from operator import add
from datetime import datetime

from langchain_core.messages import BaseMessage


class TripPlannerState(TypedDict):
    """旅行规划工作流状态"""
    
    messages: Annotated[List[BaseMessage], add]
    city: str
    start_date: str
    end_date: str
    travel_days: int
    transportation: str
    accommodation: str
    preferences: List[str]
    free_text_input: str
    
    phase: Literal["plan", "execute", "replan", "complete"]
    task_plan: Optional[Dict]
    execution_results: Dict[str, str]
    
    attraction_info: Optional[str]
    weather_info: Optional[str]
    hotel_info: Optional[str]
    transportation_info: Optional[str]
    food_info: Optional[str]
    map_info: Optional[str]
    
    trip_plan_json: Optional[str]
    error: Optional[str]
    
    cache_key: Optional[str]
    is_cached: bool
    
    user_feedback: Optional[str]
    feedback_target: Optional[str]
    
    need_user_input: bool
    pending_question: Optional[str]


class ResultCache:
    """智能缓存系统 - 基于城市+月份+偏好的缓存(提高命中率)"""
    
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self.max_size = 200
    
    def _generate_cache_key(self, city: str, month: str, preferences: List[str]) -> str:
        content = f"{city}_{month}_{'_'.join(sorted(preferences))}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get(self, cache_key: str) -> Optional[Any]:
        return self._cache.get(cache_key)
    
    def set(self, cache_key: str, data: Any, ttl: int = 7200):
        if len(self._cache) >= self.max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        
        self._cache[cache_key] = {
            "data": data,
            "timestamp": datetime.now(),
            "ttl": ttl
        }
    
    def is_valid(self, cache_key: str) -> bool:
        cached = self._cache.get(cache_key)
        if not cached:
            return False
        
        elapsed = (datetime.now() - cached["timestamp"]).total_seconds()
        return elapsed < cached["ttl"]


_result_cache = ResultCache()


def get_cache() -> ResultCache:
    """获取全局缓存实例"""
    return _result_cache
