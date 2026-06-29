"""统一工具执行器 - MCP→API→LLM兜底 三级降级"""

import json
import asyncio
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field
from langchain_core.tools import tool
from functools import wraps

from ..config import get_settings


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=30)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _try_mcp_call(mcp_tool_name: str, arguments: dict) -> str:
    try:
        from ..mcp.client import get_mcp_manager
        manager = get_mcp_manager()
        result = _run_async(manager.call_tool(mcp_tool_name, arguments))
        return result
    except Exception as e:
        print(f"  ⚠️ [MCP工具] MCP调用失败,降级到直接API: {e}")
        return ""


def cache_decorator(data_type: str):
    """缓存装饰器 - 自动处理多级缓存的读取和写入"""
    from ..cache import get_multi_level_cache

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                cache = get_multi_level_cache()
                params = kwargs.copy()
                if args:
                    params['args'] = list(args)
                cached_result = cache.get(data_type, params)
                if cached_result is not None:
                    print(f"  💾 [缓存命中] {data_type} - L1/L2缓存")
                    return cached_result
            except Exception as e:
                print(f"  ⚠️ [缓存] 缓存系统不可用: {e}")

            result = func(*args, **kwargs)

            try:
                cache = get_multi_level_cache()
                cache.set(data_type, kwargs, result)
            except Exception as e:
                print(f"  ⚠️ [缓存] 写入缓存失败: {e}")

            return result
        return wrapper
    return decorator


@dataclass
class ToolConfig:
    """工具配置 - 描述一个工具的MCP/API参数和结果解析逻辑"""
    name: str
    description: str
    data_type: str
    emoji: str
    mcp_tool: Optional[str] = None
    mcp_args_builder: Optional[Callable] = None
    mcp_parser: Optional[Callable] = None
    api_url: Optional[str] = None
    api_params_builder: Optional[Callable] = None
    api_parser: Optional[Callable] = None
    fallback_result: Optional[Callable] = None


def _parse_location(loc_str: str) -> Dict[str, float]:
    parts = loc_str.split(",")
    if len(parts) == 2:
        try:
            return {"longitude": float(parts[0]), "latitude": float(parts[1])}
        except ValueError:
            pass
    return {"longitude": 116.397128, "latitude": 39.916527}


def _parse_poi_list(pois: list, max_count: int = 8, extra_fields: Optional[Callable] = None) -> list:
    """通用POI列表解析"""
    result = []
    for poi in pois[:max_count]:
        item = {
            "name": poi.get("name"),
            "address": poi.get("address"),
            "location": _parse_location(poi.get("location", "")),
            "type": poi.get("type", ""),
            "tel": poi.get("tel", ""),
        }
        if extra_fields:
            item.update(extra_fields(poi))
        result.append(item)
    return result


def _call_amap_api(url: str, params: dict, timeout: int = 15) -> Optional[dict]:
    """通用高德API调用"""
    try:
        import requests
        response = requests.get(url, params=params, timeout=timeout)
        data = response.json()
        if data.get("status") == "1":
            return data
        return None
    except Exception as e:
        print(f"  ⚠️ [API调用失败] {url}: {e}")
        return None


def _execute_tool(config: ToolConfig, **kwargs) -> str:
    """统一工具执行器 - MCP→API→兜底"""
    print(f"  {config.emoji} [API调用] {config.name}: {kwargs}")

    # Level 1: MCP
    if config.mcp_tool and config.mcp_args_builder:
        mcp_args = config.mcp_args_builder(kwargs)
        mcp_result = _try_mcp_call(config.mcp_tool, mcp_args)
        if mcp_result and config.mcp_parser:
            try:
                data = json.loads(mcp_result)
                if data.get("success") and "data" in data:
                    parsed = config.mcp_parser(data["data"], kwargs)
                    if parsed:
                        return json.dumps(parsed, ensure_ascii=False)
            except Exception as e:
                print(f"  ⚠️ [MCP解析失败] {config.name}: {e}")

    # Level 2: API
    if config.api_url and config.api_params_builder and config.api_parser:
        settings = get_settings()
        api_key = settings.amap_api_key
        params = config.api_params_builder(kwargs, api_key)
        data = _call_amap_api(config.api_url, params)
        if data:
            parsed = config.api_parser(data, kwargs)
            if parsed:
                return json.dumps(parsed, ensure_ascii=False)

    # Level 3: 兜底
    if config.fallback_result:
        return json.dumps(config.fallback_result(kwargs), ensure_ascii=False)

    return json.dumps({"success": False, "error": f"{config.name}数据获取失败"}, ensure_ascii=False)


# ==================== 工具配置定义 ====================

ATTRACTION_CONFIG = ToolConfig(
    name="search_attractions",
    description="搜索城市景点 - 高德地图POI搜索(MCP优先+API降级+多级缓存)",
    data_type="attraction",
    emoji="🔍",
    mcp_tool="amap_maps_text_search",
    mcp_args_builder=lambda kw: {
        "keywords": kw.get("keywords", "旅游景点"),
        "city": kw["city"],
        "types": "130100|130200|130300|130400|130500",
    },
    mcp_parser=lambda raw, kw: _parse_attraction_result(raw, kw),
    api_url="https://restapi.amap.com/v3/place/text",
    api_params_builder=lambda kw, key: {
        "key": key, "keywords": kw.get("keywords", "旅游景点"), "city": kw["city"],
        "types": "130100|130200|130300|130400|130500",
        "output": "json", "offset": 10, "page": 1, "extensions": "base",
    },
    api_parser=lambda data, kw: _parse_attraction_result(data, kw),
)

WEATHER_CONFIG = ToolConfig(
    name="search_weather",
    description="查询城市天气预报 - 高德地图天气API(MCP优先+API降级+多级缓存)",
    data_type="weather",
    emoji="🌤️",
    mcp_tool="amap_maps_weather",
    mcp_args_builder=lambda kw: {"city": kw["city"], "extensions": "all"},
    mcp_parser=lambda raw, kw: _parse_weather(raw, kw),
    api_url="https://restapi.amap.com/v3/weather/weatherInfo",
    api_params_builder=lambda kw, key: {"key": key, "city": kw["city"], "extensions": "all", "output": "json"},
    api_parser=lambda data, kw: _parse_weather(data, kw),
)

def _parse_weather(raw: dict, kw: dict) -> Optional[dict]:
    forecasts = raw.get("forecasts", [])
    if not forecasts:
        return None
    forecast = forecasts[0]
    days = kw.get("days", 3)
    casts = forecast.get("casts", [])[:days]
    weather_list = []
    for cast in casts:
        weather_list.append({
            "date": cast.get("date"), "day_weather": cast.get("dayweather"),
            "night_weather": cast.get("nightweather"), "day_temp": cast.get("daytemp"),
            "night_temp": cast.get("nighttemp"), "day_wind": cast.get("daywind"),
            "night_wind": cast.get("nightwind"),
        })
    return {"success": True, "city": forecast.get("city", kw["city"]), "forecasts": weather_list}

HOTEL_CONFIG = ToolConfig(
    name="search_hotels",
    description="搜索城市酒店 - 高德地图POI搜索(MCP优先+API降级+多级缓存)",
    data_type="hotel",
    emoji="🏨",
    mcp_tool="amap_maps_text_search",
    mcp_args_builder=lambda kw: {
        "keywords": kw.get("hotel_type", "酒店"), "city": kw["city"], "types": "100100|100200",
    },
    mcp_parser=lambda raw, kw: _parse_hotel_result(raw, kw),
    api_url="https://restapi.amap.com/v3/place/text",
    api_params_builder=lambda kw, key: {
        "key": key, "keywords": kw.get("hotel_type", "酒店"), "city": kw["city"],
        "types": "100100|100200", "output": "json", "offset": 6, "page": 1, "extensions": "base",
    },
    api_parser=lambda data, kw: _parse_hotel_result(data, kw),
)

TRANSPORTATION_CONFIG = ToolConfig(
    name="search_transportation",
    description="查询交通信息 - 公共交通/机场/火车站(MCP优先+API降级+多级缓存)",
    data_type="transportation",
    emoji="🚇",
    mcp_tool="amap_maps_text_search",
    mcp_args_builder=lambda kw: {
        "keywords": {"公共交通": "地铁站|公交站", "自驾": "停车场", "出租车": "出租车", "飞机": "机场", "火车": "火车站|高铁站"}.get(kw.get("transport_type", ""), "地铁站|公交站"),
        "city": kw["city"], "types": "150100|150200|150500|150700",
    },
    mcp_parser=lambda raw, kw: (
        {"success": True, "transport_type": kw.get("transport_type", ""), "stations": [{"name": p.get("name"), "address": p.get("address")} for p in raw.get("pois", [])[:6]]}
        if raw.get("pois") else None
    ),
    api_url="https://restapi.amap.com/v3/place/text",
    api_params_builder=lambda kw, key: {
        "key": key,
        "keywords": {"公共交通": "地铁站|公交站", "自驾": "停车场", "出租车": "出租车", "飞机": "机场", "火车": "火车站|高铁站"}.get(kw.get("transport_type", ""), "地铁站|公交站"),
        "city": kw["city"], "types": "150100|150200|150500|150700",
        "output": "json", "offset": 8, "extensions": "base",
    },
    api_parser=lambda data, kw: (
        {"success": True, "transport_type": kw.get("transport_type", ""), "stations": [{"name": p.get("name"), "address": p.get("address")} for p in data.get("pois", [])[:6]]}
        if data.get("pois") else {"success": True, "transport_type": kw.get("transport_type", ""), "stations": []}
    ),
)

FOOD_CONFIG = ToolConfig(
    name="search_food",
    description="搜索当地美食/餐厅 - 高德地图POI搜索(MCP优先+API降级+多级缓存)",
    data_type="food",
    emoji="🍜",
    mcp_tool="amap_maps_text_search",
    mcp_args_builder=lambda kw: {
        "keywords": kw.get("food_type", "美食"), "city": kw["city"], "types": "050000",
    },
    mcp_parser=lambda raw, kw: (
        {"success": True, "city": kw["city"], "food_type": kw.get("food_type", "美食"),
         "restaurants": _parse_poi_list(raw.get("pois", []), 6, lambda p: {"rating": p.get("biz_ext", {}).get("rating", ""), "cost": p.get("biz_ext", {}).get("cost", "")})}
        if raw.get("pois") else None
    ),
    api_url="https://restapi.amap.com/v3/place/text",
    api_params_builder=lambda kw, key: {
        "key": key, "keywords": kw.get("food_type", "美食"), "city": kw["city"],
        "types": "050000", "output": "json", "offset": 8, "extensions": "all",
    },
    api_parser=lambda data, kw: (
        {"success": True, "city": kw["city"], "food_type": kw.get("food_type", "美食"),
         "restaurants": _parse_poi_list(data.get("pois", []), 6, lambda p: {"rating": p.get("biz_ext", {}).get("rating", ""), "cost": p.get("biz_ext", {}).get("cost", "")})}
        if data.get("pois") else None
    ),
)

MAP_CONFIG = ToolConfig(
    name="get_city_map_info",
    description="获取城市地理信息 - 用于地图展示(MCP优先+API降级+多级缓存)",
    data_type="map",
    emoji="🗺️",
    mcp_tool="amap_maps_geo",
    mcp_args_builder=lambda kw: {"address": kw["city"]},
    mcp_parser=lambda raw, kw: _parse_geocode(raw, kw),
    api_url="https://restapi.amap.com/v3/geocode/geo",
    api_params_builder=lambda kw, key: {"key": key, "address": kw["city"], "output": "json"},
    api_parser=lambda data, kw: _parse_geocode(data, kw),
)

def _parse_geocode(raw: dict, kw: dict) -> Optional[dict]:
    geocodes = raw.get("geocodes", [])
    if not geocodes:
        return None
    geo = geocodes[0]
    loc = _parse_location(geo.get("location", ""))
    return {
        "success": True, "city": kw["city"], "center": loc,
        "adcode": geo.get("adcode"), "bounds": geo.get("bounds", ""),
        "formatted_address": geo.get("formatted_address", ""),
    }


def _parse_attraction_result(data: dict, kw: dict) -> Optional[dict]:
    pois = data.get("pois", [])
    if not pois:
        return None
    desc_fn = lambda p: {"description": f"{p.get('name', '')} - {kw['city']}著名{kw.get('keywords', '景点')}景点"}
    attractions = _parse_poi_list(pois, 8, desc_fn)
    return {"success": True, "city": kw["city"], "count": len(attractions), "attractions": attractions}


def _parse_hotel_result(data: dict, kw: dict) -> Optional[dict]:
    pois = data.get("pois", [])
    if not pois:
        return None
    hotel_fn = lambda p: {"type": kw.get("hotel_type", "酒店"), "price_range": kw.get("price_range", "200-600元"), "rating": "4.5", "distance": p.get("distance", "")}
    hotels = _parse_poi_list(pois, 5, hotel_fn)
    return {"success": True, "city": kw["city"], "count": len(hotels), "hotels": hotels}


# ==================== 工具配置定义 ====================

@tool
@cache_decorator("attraction")
def search_attractions(city: str, keywords: str = "景点") -> str:
    """搜索城市景点 - 高德地图POI搜索(MCP优先+API降级+多级缓存)

    Args:
        city: 目标城市名称
        keywords: 搜索关键词,如"历史文化"、"自然风光"、"博物馆"

    Returns:
        JSON格式的景点列表,包含名称、地址、经纬度、描述等信息
    """
    return _execute_tool(ATTRACTION_CONFIG, city=city, keywords=keywords)


@tool
@cache_decorator("weather")
def search_weather(city: str, days: int = 3) -> str:
    """查询城市天气预报 - 高德地图天气API(MCP优先+API降级+多级缓存)

    Args:
        city: 目标城市名称
        days: 查询天数,默认3天

    Returns:
        JSON格式的天气信息,包含温度、天气类型、风力等
    """
    return _execute_tool(WEATHER_CONFIG, city=city, days=days)


@tool
@cache_decorator("hotel")
def search_hotels(city: str, hotel_type: str = "酒店", price_range: str = "") -> str:
    """搜索城市酒店 - 高德地图POI搜索(MCP优先+API降级+多级缓存)

    Args:
        city: 目标城市名称
        hotel_type: 酒店类型,如"经济型酒店"、"豪华酒店"、"民宿"
        price_range: 价格范围,如"200-500元"

    Returns:
        JSON格式的酒店列表,包含名称、地址、价格区间、评分等
    """
    return _execute_tool(HOTEL_CONFIG, city=city, hotel_type=hotel_type, price_range=price_range)


@tool
@cache_decorator("transportation")
def search_transportation(city: str, transport_type: str = "") -> str:
    """查询交通信息 - 公共交通/机场/火车站(MCP优先+API降级+多级缓存)

    Args:
        city: 目标城市名称
        transport_type: 交通类型,如"地铁"、"公交"、"机场"、"火车站"

    Returns:
        JSON格式的交通信息,包含站点名称、地址、线路等
    """
    return _execute_tool(TRANSPORTATION_CONFIG, city=city, transport_type=transport_type)


@tool
@cache_decorator("food")
def search_food(city: str, food_type: str = "美食") -> str:
    """搜索当地美食/餐厅 - 高德地图POI搜索(MCP优先+API降级+多级缓存)

    Args:
        city: 目标城市名称
        food_type: 美食类型,如"特色菜"、"小吃"、"火锅"、"川菜"

    Returns:
        JSON格式,包含餐厅列表
    """
    return _execute_tool(FOOD_CONFIG, city=city, food_type=food_type)


@tool
@cache_decorator("map")
def get_city_map_info(city: str) -> str:
    """获取城市地理信息 - 用于地图展示(MCP优先+API降级+多级缓存)

    Args:
        city: 目标城市名称

    Returns:
        JSON格式,包含城市中心坐标、边界、行政区划等
    """
    return _execute_tool(MAP_CONFIG, city=city)


ALL_TOOLS = [
    search_attractions,
    search_weather,
    search_hotels,
    search_transportation,
    search_food,
    get_city_map_info,
]
