"""LangChain + LangGraph 多智能体旅行规划系统 (v2.0)

模块化架构:
- state.py: 状态定义和缓存系统
- tools.py: 6个MCP工具定义
- agents.py: Agent提示词和创建函数
- nodes.py: 工作流节点函数
- 本文件: 主入口和工作流构建
"""

import json
from datetime import datetime, timedelta

from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import InMemorySaver

from ..models.schemas import TripRequest, TripPlan, DayPlan, Attraction, Meal, WeatherInfo, Location, Hotel

from .state import TripPlannerState
from .nodes import (
    plan_node,
    execute_all_tools_node,
    replan_node
)


class LangGraphTripPlannerV2:
    """
    LangChain + LangGraph 多智能体旅行规划系统 v2.0
    
    架构特点:
    - Plan-Execute-Replan 三阶段循环工作流
    - 父Agent + 6个子Agent协同架构
    - RoC(ReAct) 推理模式
    - 智能缓存与RAG复用
    - 反馈增量优化机制
    """
    
    def __init__(self):
        self.app = None
        self._build_workflow()
    
    def _build_workflow(self):
        """构建完整的工作流图"""
        print("\n" + "="*60)
        print("🏗️ [LangGraph v2.0] 开始构建工作流架构...")
        print("="*60 + "\n")
        
        workflow = StateGraph(TripPlannerState)
        
        workflow.add_node("plan", plan_node)
        workflow.add_node("execute_all", execute_all_tools_node)
        workflow.add_node("replan", replan_node)
        
        workflow.add_edge(START, "plan")
        
        def route_after_plan(state: TripPlannerState):
            phase = state.get("phase", "execute")
            if phase == "replan":
                return "replan"
            return "execute"
        
        workflow.add_conditional_edges(
            "plan",
            route_after_plan,
            {
                "execute": "execute_all",
                "replan": "replan"
            }
        )
        
        workflow.add_edge("execute_all", "replan")
        workflow.add_edge("replan", END)
        
        memory = InMemorySaver()
        self.app = workflow.compile(checkpointer=memory)
        
        print("✅ [LangGraph v2.2] 工作流构建完成!")
        print("\n工作流拓扑:")
        print("  START → plan → execute_all(并行6工具) → replan → END")
        print("  (或命中缓存时: START → plan → replan → END)")
        print("")
    
    def plan_trip(self, request: TripRequest) -> TripPlan:
        """执行旅行规划"""
        try:
            print(f"\n{'='*70}")
            print(f"🚀 [LangGraph v2.0] 开始执行旅行规划工作流")
            print(f"   📍 目的地: {request.city}")
            print(f"   📅 日期: {request.start_date} ~ {request.end_date}")
            print(f"   ⏱️ 天数: {request.travel_days}天")
            print(f"   🚗 交通: {request.transportation}")
            print(f"   🏨 住宿: {request.accommodation}")
            print(f"   ❤️ 偏好: {', '.join(request.preferences) if request.preferences else '无'}")
            print(f"{'='*70}\n")
            
            initial_state: TripPlannerState = {
                "messages": [],
                "city": request.city,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "travel_days": request.travel_days,
                "transportation": request.transportation,
                "accommodation": request.accommodation,
                "preferences": request.preferences or [],
                "free_text_input": request.free_text_input or "",
                
                "phase": "plan",
                "task_plan": None,
                "execution_results": {},
                
                "attraction_info": None,
                "weather_info": None,
                "hotel_info": None,
                "transportation_info": None,
                "food_info": None,
                "map_info": None,
                
                "trip_plan_json": None,
                "error": None,
                
                "cache_key": None,
                "is_cached": False,
                
                "user_feedback": None,
                "feedback_target": None,
                
                "need_user_input": False,
                "pending_question": None
            }
            
            config = {"configurable": {"thread_id": f"trip-v2-{request.city}-{request.start_date}"}}
            result = self.app.invoke(initial_state, config=config)
            
            trip_plan_json = result.get("trip_plan_json")
            
            if trip_plan_json:
                trip_plan = self._parse_trip_plan(trip_plan_json, request)
                
                print(f"\n{'='*70}")
                print(f"✅ [LangGraph v2.0] 旅行计划生成成功!")
                print(f"   城市: {trip_plan.city}")
                print(f"   天数: {len(trip_plan.days)}天")
                print(f"   总预算: ¥{trip_plan.budget.total if trip_plan.budget else 0}")
                print(f"{'='*70}\n")
                
                return trip_plan
            else:
                raise ValueError("工作流未生成旅行计划")
                
        except Exception as e:
            print(f"\n❌ [LangGraph v2.0] 生成旅行计划失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return self._create_fallback_plan(request)
    
    async def plan_trip_stream(self, request: TripRequest):
        """SSE流式生成旅行计划 - 实时推送进度"""
        import asyncio
        
        yield {"type": "progress", "step": "plan", "message": "📋 正在分析需求..."}
        await asyncio.sleep(0.1)
        
        try:
            initial_state: TripPlannerState = {
                "messages": [],
                "city": request.city,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "travel_days": request.travel_days,
                "transportation": request.transportation,
                "accommodation": request.accommodation,
                "preferences": request.preferences or [],
                "free_text_input": request.free_text_input or "",
                
                "phase": "execute",
                "task_plan": None,
                "execution_results": {},
                
                "attraction_info": None,
                "weather_info": None,
                "hotel_info": None,
                "transportation_info": None,
                "food_info": None,
                "map_info": None,
                
                "trip_plan_json": None,
                "error": None,
                
                "cache_key": None,
                "is_cached": False,
                
                "user_feedback": None,
                "feedback_target": None,
                
                "need_user_input": False,
                "pending_question": None
            }
            
            config = {"configurable": {"thread_id": f"trip-stream-{request.city}-{request.start_date}"}}
            result = self.app.invoke(initial_state, config=config)
            
            trip_plan_json = result.get("trip_plan_json")
            
            if trip_plan_json:
                yield {"type": "progress", "step": "replan", "message": "📝 正在整合生成计划..."}
                await asyncio.sleep(0.1)
                
                trip_plan = self._parse_trip_plan(trip_plan_json, request)
                
                yield {
                    "type": "result",
                    "success": True,
                    "data": trip_plan.model_dump()
                }
            else:
                raise ValueError("工作流未生成旅行计划")
                
        except Exception as e:
            yield {"type": "error", "message": str(e)}
    
    def update_with_feedback(self, original_request: TripRequest, feedback: str, target: str = "整体") -> TripPlan:
        """基于用户反馈增量更新旅行计划"""
        print(f"\n{'='*60}")
        print(f"🔄 [反馈调整] 收到用户反馈")
        print(f"   目标: {target}")
        print(f"   内容: {feedback}")
        print(f"{'='*60}\n")
        
        initial_state: TripPlannerState = {
            "messages": [],
            "city": original_request.city,
            "start_date": original_request.start_date,
            "end_date": original_request.end_date,
            "travel_days": original_request.travel_days,
            "transportation": original_request.transportation,
            "accommodation": original_request.accommodation,
            "preferences": original_request.preferences or [],
            "free_text_input": original_request.free_text_input or "",
            
            "phase": "replan",
            "task_plan": None,
            "execution_results": {},
            
            "attraction_info": None,
            "weather_info": None,
            "hotel_info": None,
            "transportation_info": None,
            "food_info": None,
            "map_info": None,
            
            "trip_plan_json": None,
            "error": None,
            
            "cache_key": None,
            "is_cached": False,
            
            "user_feedback": feedback,
            "feedback_target": target,
            
            "need_user_input": False,
            "pending_question": None
        }
        
        config = {"configurable": {"thread_id": f"trip-v2-feedback-{original_request.city}-{original_request.start_date}"}}
        
        try:
            result = self.app.invoke(initial_state, config=config)
            
            trip_plan_json = result.get("trip_plan_json")
            if trip_plan_json:
                print(f"✅ [反馈调整] 成功生成调整后的计划!")
                return self._parse_trip_plan(trip_plan_json, original_request)
            
            raise ValueError("工作流未返回旅行计划")
            
        except Exception as e:
            print(f"❌ [反馈调整] 执行失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return self._create_fallback_plan(original_request)
    
    def _parse_trip_plan(self, plan_json: str, request: TripRequest) -> TripPlan:
        """解析JSON为TripPlan对象"""
        try:
            data = json.loads(plan_json)
            
            days = []
            total_attractions = 0
            total_meals = 0
            total_transportation = 0
            
            for day_data in data.get("days", []):
                attractions = []
                for attr in day_data.get("attractions", []):
                    loc = attr.get("location", {})
                    attractions.append(Attraction(
                        name=attr.get("name", "未知景点"),
                        address=attr.get("address", ""),
                        location=Location(
                            longitude=loc.get("longitude", 116.397128),
                            latitude=loc.get("latitude", 39.916527)
                        ),
                        visit_duration=attr.get("visit_duration", 90),
                        description=attr.get("description", ""),
                        category=attr.get("category", "观光"),
                        rating=attr.get("rating", 4.5),
                        ticket_price=attr.get("ticket_price", 0)
                    ))
                    total_attractions += attr.get("ticket_price", 0)
                
                meals = []
                for meal in day_data.get("meals", []):
                    meals.append(Meal(
                        type=meal.get("type", "lunch"),
                        name=meal.get("name", ""),
                        address=meal.get("address", ""),
                        dishes=meal.get("dishes", []),
                        estimated_cost=meal.get("estimated_cost", 50)
                    ))
                    total_meals += meal.get("estimated_cost", 0)
                
                weather_data = day_data.get("weather", {})
                day_temp = weather_data.get("day_temp") or weather_data.get("temperature") or "20"
                night_temp = weather_data.get("night_temp") or "15"
                weather = WeatherInfo(
                    date=weather_data.get("date", day_data.get("date", "")),
                    day_weather=weather_data.get("day_weather", weather_data.get("weather_type", "")),
                    night_weather=weather_data.get("night_weather", ""),
                    day_temp=day_temp,
                    night_temp=night_temp,
                    wind_direction=weather_data.get("wind_direction", ""),
                    wind_power=weather_data.get("wind_power", ""),
                )
                
                hotel_data = day_data.get("hotel", {})
                h_loc = hotel_data.get("location", {})
                hotel = Hotel(
                    name=hotel_data.get("name", "推荐酒店"),
                    address=hotel_data.get("address", ""),
                    location=Location(
                        longitude=h_loc.get("longitude", 116.397128),
                        latitude=h_loc.get("latitude", 39.916527)
                    ),
                    price_range=hotel_data.get("price_range", "300-500元"),
                    rating=hotel_data.get("rating", "4.5"),
                    distance=hotel_data.get("distance", "1km"),
                    type=hotel_data.get("type", "经济型"),
                    estimated_cost=hotel_data.get("estimated_cost", 400)
                )
                
                budget_data = day_data.get("daily_budget", {})
                daily_budget = budget_data.get("total", 350)
                total_transportation += budget_data.get("transportation", 50)
                
                days.append(DayPlan(
                    date=day_data.get("date", ""),
                    day_index=day_data.get("day_index", 0),
                    description=day_data.get("description", ""),
                    transportation=day_data.get("transportation", request.transportation),
                    accommodation=day_data.get("accommodation", request.accommodation),
                    hotel=hotel,
                    attractions=attractions,
                    meals=meals,
                    weather=weather,
                    daily_budget=daily_budget
                ))
            
            budget_data = data.get("budget", {})
            if isinstance(budget_data, dict):
                budget_dict = budget_data
            elif hasattr(budget_data, "model_dump"):
                budget_dict = budget_data.model_dump()
            else:
                budget_dict = {}
            total_hotel = sum(d.hotel.estimated_cost for d in days if d.hotel)

            weather_info_list = []
            for w in data.get("weather_info", []):
                w_day_temp = w.get("day_temp") or w.get("temperature") or "20"
                w_night_temp = w.get("night_temp") or "15"
                weather_info_list.append(WeatherInfo(
                    date=w.get("date", ""),
                    day_weather=w.get("day_weather", w.get("weather_type", "")),
                    night_weather=w.get("night_weather", ""),
                    day_temp=w_day_temp,
                    night_temp=w_night_temp,
                    wind_direction=w.get("wind_direction", ""),
                    wind_power=w.get("wind_power", ""),
                ))
            if not weather_info_list:
                for d in days:
                    if d.weather:
                        weather_info_list.append(d.weather)

            return TripPlan(
                city=data.get("city", request.city),
                start_date=data.get("start_date", request.start_date),
                end_date=data.get("end_date", request.end_date),
                travel_days=request.travel_days,
                days=days,
                weather_info=weather_info_list,
                overall_suggestions=data.get("overall_suggestions", "祝您旅途愉快!"),
                budget={
                    "total_attractions": budget_dict.get("total_attractions", total_attractions),
                    "total_hotels": budget_dict.get("total_hotels", total_hotel),
                    "total_meals": budget_dict.get("total_meals", total_meals),
                    "total_transportation": budget_dict.get("total_transportation", total_transportation),
                    "total": budget_dict.get("total", total_attractions + total_hotel + total_meals + total_transportation)
                } if budget_dict else None
            )
            
        except Exception as e:
            print(f"❌ 解析旅行计划失败: {str(e)}")
            raise
    
    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        """创建备用计划"""
        default_location = Location(longitude=116.397128, latitude=39.916527)
        
        days = []
        for i in range(request.travel_days):
            from datetime import datetime, timedelta
            start_dt = datetime.strptime(request.start_date, "%Y-%m-%d")
            current_date = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
            
            days.append(DayPlan(
                date=current_date,
                day_index=i,
                description=f"第{i+1}天探索{request.city}",
                transportation=request.transportation,
                accommodation=request.accommodation,
                hotel=Hotel(
                    name=f"{request.city}推荐酒店",
                    address=f"{request.city}市中心",
                    location=default_location,
                    price_range="300-600元",
                    rating="4.5",
                    distance="市中心",
                    type=request.accommodation,
                    estimated_cost=400
                ),
                attractions=[
                    Attraction(
                        name=f"{request.city}热门景点{i+1}",
                        address=f"{request.city}景点地址",
                        location=default_location,
                        visit_duration=120,
                        description=f"{request.city}著名景点",
                        category="观光",
                        rating=4.5,
                        ticket_price=60
                    )
                ],
                meals=[
                    Meal(type="lunch", name="当地特色美食", estimated_cost=80),
                    Meal(type="dinner", name="晚餐推荐", estimated_cost=100)
                ],
                weather=WeatherInfo(
                    date=current_date,
                    day_weather="晴",
                    night_weather="晴",
                    day_temp=28,
                    night_temp=18,
                    wind_direction="东南",
                    wind_power="3级",
                ),
                daily_budget=500
            ))
        
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            travel_days=request.travel_days,
            days=days,
            weather_info=[d.weather for d in days if d.weather],
            overall_suggestions=f"这是为您生成的{request.city}{request.travel_days}日旅行计划备用方案。",
            budget={
                "total_attractions": 60 * request.travel_days,
                "total_hotels": 400 * request.travel_days,
                "total_meals": 180 * request.travel_days,
                "total_transportation": 100 * request.travel_days,
                "total": (60 + 400 + 180 + 100) * request.travel_days
            }
        )


_planner_instance_v2 = None


def get_langgraph_trip_planner() -> LangGraphTripPlannerV2:
    """获取 LangGraph v2.0 旅行规划器实例(单例模式)"""
    global _planner_instance_v2
    
    if _planner_instance_v2 is None:
        _planner_instance_v2 = LangGraphTripPlannerV2()
    
    return _planner_instance_v2
