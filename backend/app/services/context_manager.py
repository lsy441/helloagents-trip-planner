"""上下文管理服务 - 多轮对话上下文 + 行程参数合并 + 意图识别"""

import json
import uuid
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

from ..services.redis_session import (
    save_session, load_session, delete_session,
    save_trip_context, load_trip_context, is_redis_available
)

logger = logging.getLogger(__name__)

_memory_sessions: Dict[str, List[Dict[str, str]]] = {}
_memory_trip_contexts: Dict[str, Dict[str, Any]] = {}


def get_or_create_session(session_id: Optional[str]) -> tuple:
    if not session_id:
        session_id = str(uuid.uuid4())[:8]

    if session_id not in _memory_sessions:
        redis_messages = load_session(session_id)
        if redis_messages:
            _memory_sessions[session_id] = redis_messages
        else:
            _memory_sessions[session_id] = []

    return session_id, _memory_sessions[session_id]


def add_message(session_id: str, role: str, content: str):
    if session_id not in _memory_sessions:
        _memory_sessions[session_id] = []

    _memory_sessions[session_id].append({
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat()
    })

    save_session(session_id, _memory_sessions[session_id])


def get_history(session_id: str) -> List[Dict[str, str]]:
    if session_id not in _memory_sessions:
        redis_messages = load_session(session_id)
        if redis_messages:
            _memory_sessions[session_id] = redis_messages
        else:
            _memory_sessions[session_id] = []

    return _memory_sessions[session_id]


def clear_session(session_id: str):
    if session_id in _memory_sessions:
        del _memory_sessions[session_id]
    if session_id in _memory_trip_contexts:
        del _memory_trip_contexts[session_id]
    delete_session(session_id)


def _ensure_trip_context(session_id: str) -> Dict[str, Any]:
    if session_id not in _memory_trip_contexts:
        existing = load_trip_context(session_id)
        if existing:
            _memory_trip_contexts[session_id] = existing
        else:
            _memory_trip_contexts[session_id] = {}
    return _memory_trip_contexts[session_id]


def save_trip_params(session_id: str, params: Dict[str, Any]):
    ctx = _ensure_trip_context(session_id)
    ctx.update({
        "last_request": params,
        "updated_at": datetime.now().isoformat()
    })
    save_trip_context(session_id, ctx)


def save_trip_result(session_id: str, result: Dict[str, Any]):
    ctx = _ensure_trip_context(session_id)
    ctx.update({
        "last_result": result,
        "updated_at": datetime.now().isoformat()
    })
    save_trip_context(session_id, ctx)


def get_trip_context(session_id: str) -> Optional[Dict[str, Any]]:
    if session_id not in _memory_trip_contexts:
        existing = load_trip_context(session_id)
        if existing:
            _memory_trip_contexts[session_id] = existing
        else:
            return None

    return _memory_trip_contexts[session_id]


def get_last_trip_params(session_id: str) -> Optional[Dict[str, Any]]:
    ctx = get_trip_context(session_id)
    if ctx and "last_request" in ctx:
        return ctx["last_request"]
    return None


def get_last_trip_result(session_id: str) -> Optional[Dict[str, Any]]:
    ctx = get_trip_context(session_id)
    if ctx and "last_result" in ctx:
        return ctx["last_result"]
    return None


INTENT_PROMPT = """你是旅行规划助手的意图识别模块。分析用户消息，判断用户意图。

当前会话上下文:
{context}

对话历史(最近5条):
{history}

用户消息: {message}

返回JSON:
{{
    "intent": "new_trip / modify_trip / ask_question / clarify / chitchat",
    "extracted_params": {{
        "city": "提取的城市(如有)",
        "start_date": "提取的开始日期(如有)",
        "end_date": "提取的结束日期(如有)",
        "travel_days": "提取的天数(如有)",
        "transportation": "公共交通/自驾/步行/混合(如有)",
        "accommodation": "经济型酒店/舒适型酒店/豪华酒店/民宿(如有)",
        "preferences": ["提取的偏好标签(如有)"],
        "free_text_input": "额外需求(如有)"
    }},
    "modify_target": "修改目标(如: 天数/城市/酒店/景点/餐饮/交通/整体, 仅modify_trip时有值)",
    "modify_content": "具体修改内容描述(仅modify_trip时有值)",
    "confidence": 0.9
}}

判断标准:
- "我想去北京玩3天" → new_trip
- "改成5天" / "换个酒店" / "景点太多了" → modify_trip (上下文中有之前的行程)
- "故宫门票多少钱" → ask_question
- "刚才说的什么" → clarify
- "你好" / "谢谢" → chitchat

注意:
1. 如果用户说"改成5天"，从上下文中获取之前的行程参数，只修改天数
2. 如果用户说"换个城市"，这是new_trip而非modify_trip
3. extracted_params只提取用户明确提到的参数，不要从上下文复制"""


def recognize_intent(llm, message: str, session_id: str) -> Dict[str, Any]:
    ctx = get_trip_context(session_id)
    ctx_str = json.dumps(ctx, ensure_ascii=False, default=str) if ctx else "{}"

    history = get_history(session_id)
    recent = history[-5:] if len(history) > 5 else history
    history_str = "\n".join(
        [f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content', '')}" for m in recent]
    )

    prompt = INTENT_PROMPT.format(
        context=ctx_str,
        history=history_str,
        message=message
    )

    try:
        response = llm.invoke(prompt)
        content = response.content

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        json_match = re.search(r'\{[\s\S]*\}', content.strip())
        result = json.loads(json_match.group(0)) if json_match else {"reply": content}

        result.setdefault("intent", "ask_question")
        result.setdefault("extracted_params", {})
        result.setdefault("confidence", 0.5)

        return result
    except Exception as e:
        logger.error(f"Intent recognition failed: {e}")
        return {
            "intent": "ask_question",
            "extracted_params": {},
            "modify_target": "",
            "modify_content": "",
            "confidence": 0.0
        }


def merge_params(session_id: str, new_params: Dict[str, Any]) -> Dict[str, Any]:
    last_params = get_last_trip_params(session_id)

    if not last_params:
        return new_params

    merged = dict(last_params)

    param_map = {
        "city": "city",
        "start_date": "start_date",
        "end_date": "end_date",
        "travel_days": "travel_days",
        "transportation": "transportation",
        "accommodation": "accommodation",
        "free_text_input": "free_text_input",
    }

    for param_key, field_name in param_map.items():
        value = new_params.get(param_key)
        if value is not None and value != "" and value != 0:
            merged[field_name] = value

    if "preferences" in new_params and new_params["preferences"]:
        existing_prefs = merged.get("preferences", [])
        if isinstance(existing_prefs, list):
            merged_prefs = list(set(existing_prefs + new_params["preferences"]))
            merged["preferences"] = merged_prefs
        else:
            merged["preferences"] = new_params["preferences"]

    return merged


def build_context_summary(session_id: str) -> str:
    ctx = get_trip_context(session_id)
    if not ctx:
        return "无上下文信息"

    parts = []

    if "last_request" in ctx:
        req = ctx["last_request"]
        parts.append(f"上次行程: {req.get('city', '?')} {req.get('travel_days', '?')}天 "
                     f"({req.get('start_date', '?')} ~ {req.get('end_date', '?')})")
        if req.get("preferences"):
            parts.append(f"偏好: {', '.join(req['preferences'])}")
        if req.get("transportation"):
            parts.append(f"交通: {req['transportation']}")
        if req.get("accommodation"):
            parts.append(f"住宿: {req['accommodation']}")

    if "last_result" in ctx:
        result = ctx["last_result"]
        if isinstance(result, dict) and "city" in result:
            days_count = len(result.get("days", []))
            parts.append(f"已生成行程: {days_count}天计划")

    return " | ".join(parts) if parts else "无上下文信息"
