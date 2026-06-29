"""LangGraph工作流节点函数 - Plan/Execute/Replan (v3: ReAct + 可观测性)"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from .state import TripPlannerState, get_cache
from .agents import create_llm, PLANNER_AGENT_PROMPT
from .tools import (
    search_attractions,
    search_weather,
    search_hotels,
    search_transportation,
    search_food,
    get_city_map_info
)
from .react_agent import search_attractions_with_react
from ..services.observability import timer, logger, get_metrics_collector


@timer("plan_node")
def plan_node(state: TripPlannerState) -> dict:
    """Plan阶段 - 父Agent进行任务分解和规划"""
    logger.info(f"[PLAN] 开始任务规划: {state['city']}")
    
    cache = get_cache()
    cache_key = cache._generate_cache_key(
        state["city"],
        state["start_date"][:7],
        state["preferences"]
    )
    
    if cache.is_valid(cache_key):
        cached_data = cache.get(cache_key)
        logger.info(f"[PLAN] 命中缓存,直接复用历史结果")
        return {
            "phase": "replan",
            "execution_results": cached_data.get("execution_results", {}),
            "cache_key": cache_key,
            "is_cached": True
        }
    
    llm = create_llm()
    
    analysis_prompt = f"""请分析以下旅行需求,生成任务执行计划:

目的地: {state['city']}
日期: {state['start_date']} 至 {state['end_date']}
天数: {state['travel_days']}
交通方式: {state['transportation']}
住宿类型: {state['accommodation']}
用户偏好: {', '.join(state['preferences']) if state['preferences'] else '无特殊偏好'}
其他需求: {state['free_text_input'] or '无'}

请返回JSON格式的任务计划:
{{
    "tasks": [
        {{"agent": "景点专家", "priority": 1, "description": "..."}},
        {{"agent": "天气专家", "priority": 1, "description": "..."}},
        ...
    ],
    "need_user_input": false,
    "suggestions": "..."
}}"""
    
    response = llm.invoke([HumanMessage(content=analysis_prompt)])
    
    try:
        task_plan = json.loads(response.content)
    except:
        task_plan = {
            "tasks": [
                {"agent": "景点专家", "priority": 1},
                {"agent": "天气专家", "priority": 1},
                {"agent": "酒店专家", "priority": 2},
                {"agent": "交通专家", "priority": 2},
                {"agent": "美食专家", "priority": 3},
                {"agent": "地图专家", "priority": 3}
            ],
            "need_user_input": False
        }
    
    logger.info(f"[PLAN] 任务规划完成,共{len(task_plan.get('tasks', []))}个子任务")
    
    return {
        "phase": "execute",
        "task_plan": task_plan,
        "cache_key": cache_key,
        "is_cached": False,
        "execution_results": {}
    }


def _execute_tool_sync(tool_name: str, tool_func) -> tuple[str, str]:
    """同步执行单个工具"""
    metrics = get_metrics_collector()
    start = time.perf_counter()
    try:
        result = tool_func()
        if isinstance(result, dict):
            result = json.dumps(result, ensure_ascii=False)
        elapsed = time.perf_counter() - start
        metrics.record_time(f"tool_{tool_name}", elapsed)
        logger.info(f"[EXECUTE] {tool_name} 完成 ({elapsed:.3f}s)")
        return tool_name, result
    except Exception as e:
        elapsed = time.perf_counter() - start
        metrics.record_time(f"tool_{tool_name}", elapsed)
        logger.error(f"[EXECUTE] {tool_name} 异常 ({elapsed:.3f}s): {e}")
        return tool_name, json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@timer("execute_all_tools_node")
def execute_all_tools_node(state: TripPlannerState) -> dict:
    """并行Execute节点 - 使用ThreadPoolExecutor同时调用6个工具,大幅缩短耗时"""
    logger.info(f"[EXECUTE] 开始并行执行6个工具: {state['city']}")
    
    city = state['city']
    preferences = state.get('preferences', [])
    
    tool_tasks = [
        ("attraction", lambda: search_attractions_with_react(city, preferences)),
        ("weather", lambda: search_weather.invoke({"city": city, "days": state.get('travel_days', 3)})),
        ("hotel", lambda: search_hotels.invoke({"city": city, "hotel_type": state.get('accommodation', '酒店')})),
        ("transportation", lambda: search_transportation.invoke({"city": city, "transport_type": state.get('transportation', '公共交通')})),
        ("food", lambda: search_food.invoke({"city": city, "food_type": "美食"})),
        ("map", lambda: get_city_map_info.invoke({"city": city}))
    ]
    
    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_tool = {
            executor.submit(_execute_tool_sync, name, func): name 
            for name, func in tool_tasks
        }
        for future in as_completed(future_to_tool):
            tool_name = future_to_tool[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f"[EXECUTE] {tool_name} 执行失败: {e}")
                results.append((tool_name, json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)))
    
    execution_results = {}
    field_map = {
        "attraction": "attraction_info",
        "weather": "weather_info",
        "hotel": "hotel_info",
        "transportation": "transportation_info",
        "food": "food_info",
        "map": "map_info"
    }
    
    messages = []
    label_map = {
        "attraction": "🔍", "weather": "☁️", "hotel": "🏨",
        "transportation": "🚗", "food": "🍜", "map": "🗺️"
    }
    
    update_dict = {"execution_results": execution_results}
    
    for tool_name, output in results:
        execution_results[tool_name] = output
        update_dict[field_map[tool_name]] = output
        messages.append(AIMessage(content=f"[{label_map[tool_name]}结果]\n{output}"))
    
    update_dict["messages"] = messages
    
    logger.info(f"[EXECUTE] 全部6个工具并行执行完成")
    
    return update_dict


@timer("replan_node")
def replan_node(state: TripPlannerState) -> dict:
    """Replan阶段 - 整合所有结果,生成最终旅行计划"""
    logger.info(f"[REPLAN] 开始整合生成旅行计划: {state['city']}")
    
    llm = create_llm()
    
    input_parts = [
        f"**目的地:** {state['city']}",
        f"**日期:** {state['start_date']} 至 {state['end_date']}",
        f"**天数:** {state['travel_days']}天",
        f"**交通方式:** {state['transportation']}",
        f"**住宿类型:** {state['accommodation']}",
        f"**用户偏好:** {', '.join(state['preferences']) if state['preferences'] else '无'}",
        f"**其他需求:** {state['free_text_input'] or '无'}",
        "",
        "**=== 子Agent执行结果 ===**",
        "",
        f"**[景点搜索]**\n{state.get('attraction_info', '暂无')}",
        "",
        f"**[天气查询]**\n{state.get('weather_info', '暂无')}",
        "",
        f"**[酒店推荐]**\n{state.get('hotel_info', '暂无')}",
        "",
        f"**[交通方案]**\n{state.get('transportation_info', '暂无')}",
        "",
        f"**[美食推荐]**\n{state.get('food_info', '暂无')}",
        "",
        f"**[地图信息]**\n{state.get('map_info', '暂无')}",
    ]
    
    if state.get("user_feedback"):
        input_parts.extend([
            "",
            "**=== 用户反馈(需调整部分) ===**",
            f"**调整目标:** {state.get('feedback_target', '整体')}",
            f"**反馈内容:** {state['user_feedback']}",
            "",
            "请针对以上反馈进行调整,其他未提及的部分保持不变!"
        ])
    
    input_text = "\n".join(input_parts)
    input_text += "\n\n请严格按照JSON格式返回完整的旅行计划。"
    
    result = llm.invoke([
        SystemMessage(content=PLANNER_AGENT_PROMPT),
        HumanMessage(content=input_text)
    ])
    content = result.content
    
    plan_json = _extract_json_from_response(content)
    
    cache = get_cache()
    cache_key = state.get("cache_key")
    if cache_key and not state.get("is_cached"):
        cache.set(cache_key, {
            "execution_results": state.get("execution_results", {}),
            "trip_plan_json": plan_json
        })
        logger.info(f"[REPLAN] 结果已存入缓存")
    
    logger.info(f"[REPLAN] 旅行计划生成完成")
    
    return {
        "phase": "complete",
        "messages": [AIMessage(content=f"[最终旅行计划]\n{content}")],
        "trip_plan_json": plan_json
    }


def _extract_json_from_response(content: str) -> str:
    """从响应中提取JSON"""
    patterns = [
        r'```json\s*([\s\S]*?)\s*```',
        r'```\s*([\s\S]*?)\s*```',
        r'(\{[\s\S]*\})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            json_str = match.group(1).strip()
            try:
                parsed = json.loads(json_str)
                return json.dumps(parsed, ensure_ascii=False)
            except:
                pass
    
    return content
