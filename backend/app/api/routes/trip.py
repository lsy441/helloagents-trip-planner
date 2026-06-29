"""旅行规划API路由 - LangChain + LangGraph v2.1 (SSE流式) + 上下文管理"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional
import json
import asyncio

from ...models.schemas import (
    TripRequest,
    TripPlanResponse
)
from ...services.context_manager import (
    get_or_create_session, add_message,
    save_trip_params, save_trip_result, get_last_trip_params
)

router = APIRouter(prefix="/trip", tags=["旅行规划"])


def _get_planner():
    from ...agents.trip_planner_langgraph import get_langgraph_trip_planner
    return get_langgraph_trip_planner()


class FeedbackRequest(BaseModel):
    original_request: TripRequest = Field(..., description="原始旅行请求")
    feedback: str = Field(..., description="用户反馈内容")
    target: Optional[str] = Field(default="整体", description="调整目标,如'第2天','酒店','餐饮'")


class ContextualTripRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, description="会话ID,用于上下文管理")
    trip_request: TripRequest = Field(..., description="旅行规划请求")


@router.post(
    "/plan-stream",
    summary="生成旅行计划(流式)",
    description="SSE流式返回生成进度和最终结果"
)
async def plan_trip_stream(request: TripRequest, session_id: Optional[str] = None):
    """SSE流式生成旅行计划"""
    sid, _ = get_or_create_session(session_id)
    save_trip_params(sid, request.model_dump())

    add_message(sid, "user",
        f"规划行程: {request.city} {request.travel_days}天 "
        f"({request.start_date}~{request.end_date}) "
        f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'start', 'message': '开始规划...', 'session_id': sid}, ensure_ascii=False)}\n\n"

            planner = _get_planner()

            result_data = None
            async for progress in planner.plan_trip_stream(request):
                yield f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"
                if progress.get("type") == "result" and progress.get("data"):
                    result_data = progress["data"]
                await asyncio.sleep(0.01)

            if result_data:
                save_trip_result(sid, result_data)
                add_message(sid, "assistant", f"已生成{request.city}{request.travel_days}天行程计划")

            yield f"data: {json.dumps({'type': 'complete', 'message': '规划完成', 'session_id': sid}, ensure_ascii=False)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="""
    根据用户输入的旅行需求,生成详细的旅行计划

    **上下文管理:**
    - 传入session_id可关联会话上下文
    - 系统自动保存行程参数和结果
    - 后续可通过"改成5天"等自然语言修改行程

    **工作流架构 (LangGraph v2.0):**
    - Plan阶段: 父Agent分析需求,生成任务计划
    - Execute阶段: 6个子Agent并行/串行执行
    - Replan阶段: 整合结果,智能检错,生成最终计划
    """
)
async def plan_trip(request: TripRequest, session_id: Optional[str] = None):
    """生成旅行计划"""
    try:
        sid, _ = get_or_create_session(session_id)
        save_trip_params(sid, request.model_dump())

        add_message(sid, "user",
            f"规划行程: {request.city} {request.travel_days}天 "
            f"({request.start_date}~{request.end_date}) "
            f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")

        print(f"\n{'='*60}")
        print(f"[Trip] session_id={sid}")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        planner = _get_planner()
        trip_plan = planner.plan_trip(request)

        result_dict = trip_plan.model_dump() if hasattr(trip_plan, 'model_dump') else trip_plan
        save_trip_result(sid, result_dict)
        add_message(sid, "assistant", f"已生成{request.city}{request.travel_days}天行程计划")

        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan,
            session_id=sid
        )

    except Exception as e:
        print(f"[ERROR] plan_trip failed: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.post(
    "/plan-with-context",
    response_model=TripPlanResponse,
    summary="带上下文的行程规划",
    description="""
    支持上下文管理的行程规划接口:
    - 首次规划: 传入完整参数
    - 修改行程: 传入session_id + 修改参数,自动合并上下文
    - 示例: 用户说"改成5天",只需传 travel_days=5, 其他参数从上下文获取
    """
)
async def plan_with_context(req: ContextualTripRequest):
    """带上下文的行程规划"""
    try:
        sid, _ = get_or_create_session(req.session_id)

        last_params = get_last_trip_params(sid)
        if last_params:
            merged = dict(last_params)
            new_params = req.trip_request.model_dump()
            for key, value in new_params.items():
                if value is not None and value != "" and value != 0 and value != []:
                    merged[key] = value
            merged_request = TripRequest(**merged)
        else:
            merged_request = req.trip_request

        save_trip_params(sid, merged_request.model_dump())

        add_message(sid, "user",
            f"规划行程: {merged_request.city} {merged_request.travel_days}天 "
            f"({merged_request.start_date}~{merged_request.end_date})")

        planner = _get_planner()
        trip_plan = planner.plan_trip(merged_request)

        result_dict = trip_plan.model_dump() if hasattr(trip_plan, 'model_dump') else trip_plan
        save_trip_result(sid, result_dict)
        add_message(sid, "assistant", f"已生成{merged_request.city}{merged_request.travel_days}天行程计划")

        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan,
            session_id=sid
        )

    except Exception as e:
        print(f"[ERROR] plan_with_context failed: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.post(
    "/feedback",
    response_model=TripPlanResponse,
    summary="基于反馈调整计划",
    description="""
    基于用户反馈增量调整旅行计划

    **使用示例:**
    - target="第2天", feedback="景点太多,减少到2个"
    - target="酒店", feedback="换一家更便宜的"
    - target="餐饮", feedback="增加当地特色小吃推荐"
    """
)
async def update_with_feedback(feedback_req: FeedbackRequest, session_id: Optional[str] = None):
    """基于反馈更新旅行计划"""
    try:
        sid, _ = get_or_create_session(session_id)

        add_message(sid, "user",
            f"调整行程: {feedback_req.target} - {feedback_req.feedback}")

        planner = _get_planner()

        trip_plan = planner.update_with_feedback(
            original_request=feedback_req.original_request,
            feedback=feedback_req.feedback,
            target=feedback_req.target
        )

        result_dict = trip_plan.model_dump() if hasattr(trip_plan, 'model_dump') else trip_plan
        save_trip_result(sid, result_dict)
        save_trip_params(sid, feedback_req.original_request.model_dump())
        add_message(sid, "assistant", f"已调整行程: {feedback_req.target}")

        return TripPlanResponse(
            success=True,
            message="根据反馈调整完成",
            data=trip_plan,
            session_id=sid
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"调整失败: {str(e)}"
        )


@router.get(
    "/context/{session_id}",
    summary="获取会话上下文",
    description="获取指定会话的行程上下文信息"
)
async def get_context(session_id: str):
    from ...services.context_manager import get_trip_context, build_context_summary
    ctx = get_trip_context(session_id)
    if not ctx:
        return {"session_id": session_id, "context": None, "summary": "无上下文"}
    return {
        "session_id": session_id,
        "context": ctx,
        "summary": build_context_summary(session_id)
    }


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
async def health_check():
    try:
        planner = _get_planner()

        return {
            "status": "healthy",
            "service": "trip-planner-v2.0",
            "framework": "LangChain + LangGraph",
            "architecture": {
                "pattern": "Plan-Execute-Replan",
                "features": [
                    "RoC reasoning",
                    "Smart Cache (L1+L2)",
                    "Feedback optimization",
                    "Context management"
                ]
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Service unavailable: {str(e)}"
        )
