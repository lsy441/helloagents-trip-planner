"""对话API - 多轮对话 + 智能交互引擎 + 意图识别 + 上下文联动"""

import json
import uuid
import asyncio
import logging
from typing import Optional, List, Dict
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ...agents.agents import create_llm
from ...memory.compressor import get_compressor
from ...services.context_manager import (
    get_or_create_session, add_message, get_history,
    clear_session as ctx_clear_session,
    recognize_intent, merge_params, build_context_summary,
    get_last_trip_params, get_last_trip_result, save_trip_params
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["智能对话"])


class ChatMessage(BaseModel):
    role: str = Field(..., description="角色: user/assistant/system")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, description="会话ID")
    message: str = Field(..., description="用户消息")
    context: Optional[Dict] = Field(default=None, description="上下文信息")
    history: Optional[List[ChatMessage]] = Field(default=[], description="对话历史")
    page: Optional[str] = Field(default="home", description="当前页面: home/result")


class ChatAction(BaseModel):
    type: str = Field(default="", description="动作类型: fill_form/adjust_plan/navigate/generate_trip")
    data: Optional[Dict] = Field(default=None, description="动作数据")


class ChatResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    reply: str = Field(..., description="AI回复")
    need_user_input: bool = Field(default=False, description="是否需要用户补充信息")
    pending_question: Optional[str] = Field(default=None, description="待回答问题")
    quick_replies: Optional[List[str]] = Field(default=None, description="快捷回复选项")
    action: Optional[ChatAction] = Field(default=None, description="联动动作")


class InteractionCheckRequest(BaseModel):
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    travel_days: int = Field(..., description="旅行天数")
    transportation: str = Field(default="", description="交通方式")
    accommodation: str = Field(default="", description="住宿偏好")
    preferences: List[str] = Field(default=[], description="偏好标签")
    free_text_input: str = Field(default="", description="额外需求")


INTERACTION_CHECK_PROMPT = """你是旅行规划助手,正在分析用户需求是否足够生成旅行计划。

用户需求:
- 目的地: {city}
- 日期: {start_date} 至 {end_date} ({travel_days}天)
- 交通: {transportation}
- 住宿: {accommodation}
- 偏好: {preferences}
- 额外需求: {free_text_input}

请判断是否需要向用户询问更多信息。只有当关键信息严重缺失时才询问。

返回JSON:
{{
    "need_input": false,
    "question": "",
    "quick_replies": []
}}

判断标准:
- 只知道城市和日期,其他全空 → 需要询问偏好
- 有城市+日期+至少1个偏好 → 不需要询问
- free_text_input中有明确需求 → 不需要询问"""


HOME_PAGE_PROMPT = """你是智能旅行规划助手，用户正在首页填写旅行规划表单。

当前会话上下文:
{context}

用户当前表单状态:
{form_state}

你的任务:
1. 理解用户的旅行意图
2. 如果用户表达了具体偏好（如城市、交通、住宿、偏好标签等），提取出来作为表单填充动作
3. 如果用户说"帮我规划"、"开始吧"、"生成"等，触发导航到结果页
4. 如果信息不足，友好地追问

返回JSON格式:
{{
    "reply": "你的回复文本",
    "action": {{
        "type": "fill_form 或 navigate 或 none",
        "data": {{
            "city": "提取的城市(如有)",
            "transportation": "公共交通/自驾/步行/混合(如有)",
            "accommodation": "经济型酒店/舒适型酒店/豪华酒店/民宿(如有)",
            "preferences": ["历史文化", "自然风光", ...],
            "free_text_input": "额外需求(如有)",
            "navigate_to": "result(如需跳转)"
        }}
    }}
}}

示例:
用户: "我想去西安玩3天，喜欢历史和美食"
回复: {{"reply": "好的！西安是历史文化名城，我来帮你填写偏好。", "action": {{"type": "fill_form", "data": {{"city": "西安", "preferences": ["历史文化", "美食"]}}}}}}

用户: "帮我生成计划"
回复: {{"reply": "好的，正在为你生成旅行计划！", "action": {{"type": "navigate", "data": {{"navigate_to": "result"}}}}}}

用户: "北京有什么好玩的"
回复: {{"reply": "北京有很多精彩景点！**故宫**和**长城**是必去的，还有颐和园、天坛等。你喜欢哪类景点？", "action": {{"type": "fill_form", "data": {{"city": "北京"}}}}}}
"""


RESULT_PAGE_PROMPT = """你是智能旅行规划助手，用户正在查看已生成的旅行计划。

当前会话上下文:
{context}

当前旅行计划概要:
{plan_summary}

你的任务:
1. 根据用户的调整需求，触发AI智能调整
2. 如果用户想修改行程（如减少景点、换酒店、改餐饮等），提取调整目标和内容
3. 如果用户只是问问题，正常回答
4. 如果用户想重新规划，触发新建行程

返回JSON格式:
{{
    "reply": "你的回复文本",
    "action": {{
        "type": "adjust_plan 或 generate_trip 或 none",
        "data": {{
            "target": "整体/景点/酒店/餐饮/交通/第N天",
            "feedback": "具体的调整需求描述",
            "trip_params": {{}}
        }}
    }}
}}

示例:
用户: "景点太多了，每天少安排几个"
回复: {{"reply": "好的，我来帮你减少每天的景点数量，让行程更轻松！", "action": {{"type": "adjust_plan", "data": {{"target": "景点", "feedback": "景点太多,每天减少到2-3个"}}}}}}

用户: "换个便宜点的酒店"
回复: {{"reply": "没问题，我来帮你找性价比更高的酒店！", "action": {{"type": "adjust_plan", "data": {{"target": "酒店", "feedback": "换一家更便宜的酒店"}}}}}}

用户: "改成5天"
回复: {{"reply": "好的，我来帮你调整为5天行程！", "action": {{"type": "generate_trip", "data": {{"trip_params": {{"travel_days": 5}}}}}}}}

用户: "西安有什么特色小吃"
回复: {{"reply": "西安的特色小吃非常多！**肉夹馍**、**凉皮**、**羊肉泡馍**、**biangbiang面**都是必尝的。回民街是美食集中地。", "action": {{"type": "none", "data": null}}}}
"""


def _parse_json_response(content: str) -> dict:
    try:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        content = content.strip()

        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            content = json_match.group(0)

        return json.loads(content)
    except:
        return {"reply": content, "action": {"type": "none", "data": None}}


@router.post("/check-interaction", summary="智能交互引擎 - 判断是否需要用户补充信息")
async def check_interaction(req: InteractionCheckRequest):
    try:
        llm = create_llm()
        prompt = INTERACTION_CHECK_PROMPT.format(
            city=req.city, start_date=req.start_date, end_date=req.end_date,
            travel_days=req.travel_days, transportation=req.transportation or "未指定",
            accommodation=req.accommodation or "未指定",
            preferences=", ".join(req.preferences) if req.preferences else "未指定",
            free_text_input=req.free_text_input or "无",
        )

        response = llm.invoke(prompt)
        content = response.content

        result = _parse_json_response(content)
        if "need_input" not in result:
            result = {"need_input": False, "question": "", "quick_replies": []}

        return {
            "need_user_input": result.get("need_input", False),
            "pending_question": result.get("question", ""),
            "quick_replies": result.get("quick_replies", []),
        }
    except Exception as e:
        return {"need_user_input": False, "pending_question": "", "quick_replies": [], "error": str(e)}


@router.post("/message", response_model=ChatResponse, summary="发送对话消息(支持意图识别+上下文联动)")
async def send_message(req: ChatRequest):
    try:
        session_id, _ = get_or_create_session(req.session_id)
        add_message(session_id, "user", req.message)

        llm = create_llm()
        compressor = get_compressor()
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

        context_summary = build_context_summary(session_id)
        form_state = json.dumps(req.context, ensure_ascii=False) if req.context else "{}"

        page = req.page or "home"
        if page == "result":
            last_result = get_last_trip_result(session_id)
            plan_summary = "{}"
            if last_result:
                try:
                    if isinstance(last_result, dict):
                        plan_summary = json.dumps({
                            "city": last_result.get("city", ""),
                            "days_count": len(last_result.get("days", [])),
                            "budget": last_result.get("budget", {})
                        }, ensure_ascii=False)
                except:
                    plan_summary = "{}"

            system_prompt = RESULT_PAGE_PROMPT.format(
                context=context_summary,
                plan_summary=plan_summary
            )
        else:
            system_prompt = HOME_PAGE_PROMPT.format(
                context=context_summary,
                form_state=form_state
            )

        lc_messages = [SystemMessage(content=system_prompt)]

        history = get_history(session_id)
        for msg in history[-10:]:
            if msg.get("role") == "user":
                lc_messages.append(HumanMessage(content=msg.get("content", "")))
            elif msg.get("role") == "assistant":
                lc_messages.append(AIMessage(content=msg.get("content", "")))

        lc_messages = compressor.compress(lc_messages)

        response = llm.invoke(lc_messages)
        content = response.content

        logger.info(f"[Chat] LLM response: {content[:300]}")

        parsed = _parse_json_response(content)

        reply = parsed.get("reply", content)
        action_data = parsed.get("action", {"type": "none", "data": None})

        action: Optional[ChatAction] = None
        if action_data and action_data.get("type") and action_data["type"] != "none":
            action_type = action_data["type"]
            action_payload = action_data.get("data") or {}

            if action_type == "generate_trip":
                trip_params = action_payload.get("trip_params", {})
                last_params = get_last_trip_params(session_id)
                if last_params:
                    merged = merge_params(session_id, trip_params)
                    action_payload["trip_params"] = merged
                else:
                    action_payload["trip_params"] = trip_params

            action = ChatAction(type=action_type, data=action_payload)

        add_message(session_id, "assistant", reply)

        return ChatResponse(
            session_id=session_id,
            reply=reply,
            need_user_input=False,
            pending_question=None,
            quick_replies=None,
            action=action,
        )
    except Exception as e:
        import traceback
        error_detail = f"对话失败: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_detail)
        raise HTTPException(status_code=500, detail=f"对话失败: {str(e)}")


@router.post("/intent", summary="意图识别接口")
async def detect_intent(req: ChatRequest):
    try:
        session_id, _ = get_or_create_session(req.session_id)
        llm = create_llm()
        result = recognize_intent(llm, req.message, session_id)
        return {"session_id": session_id, "intent": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"意图识别失败: {str(e)}")


@router.get("/stream", summary="SSE流式对话")
async def stream_chat(session_id: Optional[str] = None, message: str = ""):
    async def event_generator():
        sid, _ = get_or_create_session(session_id)

        yield {"event": "start", "data": json.dumps({"session_id": sid})}

        try:
            llm = create_llm()
            from langchain_core.messages import HumanMessage, SystemMessage

            context_summary = build_context_summary(sid)
            lc_messages = [
                SystemMessage(content=f"你是智能旅行规划助手。\n\n当前上下文: {context_summary}"),
                HumanMessage(content=message),
            ]

            add_message(sid, "user", message)

            response = llm.invoke(lc_messages)
            reply = response.content

            add_message(sid, "assistant", reply)

            chunk_size = 20
            for i in range(0, len(reply), chunk_size):
                chunk = reply[i:i + chunk_size]
                yield {"event": "chunk", "data": json.dumps({"content": chunk}, ensure_ascii=False)}
                await asyncio.sleep(0.05)

            yield {"event": "done", "data": json.dumps({"session_id": sid}, ensure_ascii=False)}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


@router.get("/sessions", summary="获取所有会话列表")
async def get_all_sessions():
    from ...services.redis_session import list_sessions as redis_list_sessions
    redis_sessions = redis_list_sessions()
    if redis_sessions:
        return {"sessions": redis_sessions, "storage": "redis"}

    from ...services.context_manager import _memory_sessions
    sessions = []
    for sid, msgs in _memory_sessions.items():
        if not msgs:
            continue
        first_user_msg = ""
        for m in msgs:
            if m.get("role") == "user":
                first_user_msg = m.get("content", "")[:30]
                break
        sessions.append({
            "session_id": sid,
            "title": first_user_msg or f"Session {sid}",
            "message_count": len(msgs),
            "updated_at": None,
        })
    return {"sessions": sessions, "storage": "memory"}


@router.get("/sessions/{session_id}", summary="获取会话历史")
async def get_session(session_id: str):
    history = get_history(session_id)
    return {"session_id": session_id, "messages": history}


@router.delete("/sessions/{session_id}", summary="清除会话")
async def clear_session(session_id: str):
    ctx_clear_session(session_id)
    return {"success": True, "message": "会话已清除"}


@router.get("/context/{session_id}", summary="获取会话上下文摘要")
async def get_context_summary(session_id: str):
    summary = build_context_summary(session_id)
    last_params = get_last_trip_params(session_id)
    last_result = get_last_trip_result(session_id)
    return {
        "session_id": session_id,
        "summary": summary,
        "has_previous_trip": last_params is not None,
        "last_params": last_params,
        "has_result": last_result is not None
    }
