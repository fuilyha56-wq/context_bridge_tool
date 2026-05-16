"""私聊与群聊上下文互通工具。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Annotated, Any

from src.core.components import BaseTool
from src.core.models.sql_alchemy import ChatStreams, Messages
from src.core.utils.user_query_helper import get_user_query_helper
from src.kernel.db import QueryBuilder
from src.kernel.logger import get_logger

from .config import ContextBridgeToolConfig

logger = get_logger("context_bridge_tool.tools")


_PRIVATE = "private"
_GROUP = "group"
_ALL = "all"
_MEMORY_PERSON_TYPE = "person"


def _get_config(plugin: Any) -> ContextBridgeToolConfig:
    """从插件实例读取配置，失败时回退默认配置。"""

    config = getattr(plugin, "config", None)
    if isinstance(config, ContextBridgeToolConfig):
        return config
    return ContextBridgeToolConfig()


def _clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    """将整数压入安全范围，别传夸张数字啊笨蛋。"""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def _normalize_list(values: list[str]) -> set[str]:
    """规范化配置中的字符串列表。"""

    return {str(item).strip().lower() for item in values if str(item).strip()}


def _normalize_chat_scope(chat_scope: str, config: ContextBridgeToolConfig) -> str:
    """规范化聊天范围，并应用配置允许列表。"""

    normalized = str(chat_scope or _ALL).strip().lower()
    if normalized not in {_PRIVATE, _GROUP, _ALL}:
        normalized = _ALL

    allowed = _normalize_list(config.access.allowed_chat_scopes)
    if allowed and normalized not in allowed:
        if _ALL in allowed:
            return _ALL
        if _PRIVATE in allowed:
            return _PRIVATE
        if _GROUP in allowed:
            return _GROUP
        return ""
    return normalized


def _normalize_text(value: str | None) -> str:
    """清理模型传入的文本参数。"""

    return str(value or "").strip()


def _format_time(value: Any) -> str:
    """格式化 Unix 时间戳。"""

    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value or "")


def _content_preview(value: Any, max_chars: int) -> str:
    """生成消息正文预览。"""

    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)] + "…"


def _identity_values(*, platform: str, user_id: str, person_id: str) -> set[str]:
    """生成黑白名单可匹配的用户身份集合。"""

    return {
        _normalize_text(user_id).lower(),
        f"{_normalize_text(platform).lower()}:{_normalize_text(user_id).lower()}",
        _normalize_text(person_id).lower(),
    }


def _ensure_platform_allowed(config: ContextBridgeToolConfig, platform: str) -> tuple[bool, str]:
    """检查平台黑白名单。"""

    normalized = _normalize_text(platform).lower()
    allowlist = _normalize_list(config.access.platform_allowlist)
    blocklist = _normalize_list(config.access.platform_blocklist)
    if normalized in blocklist:
        return False, f"平台 {platform} 位于黑名单"
    if allowlist and normalized not in allowlist:
        return False, f"平台 {platform} 不在白名单"
    return True, ""


def _ensure_user_allowed(
    config: ContextBridgeToolConfig,
    *,
    platform: str,
    user_id: str,
    person_id: str,
) -> tuple[bool, str]:
    """检查用户黑白名单。"""

    identities = _identity_values(platform=platform, user_id=user_id, person_id=person_id)
    allowlist = _normalize_list(config.access.user_allowlist)
    blocklist = _normalize_list(config.access.user_blocklist)
    if identities & blocklist:
        return False, "目标用户位于黑名单"
    if allowlist and not identities & allowlist:
        return False, "目标用户不在白名单"
    return True, ""


def _ensure_group_allowed(config: ContextBridgeToolConfig, group_id: str) -> tuple[bool, str]:
    """检查群黑白名单。"""

    normalized = _normalize_text(group_id).lower()
    if not normalized:
        return True, ""
    allowlist = _normalize_list(config.access.group_allowlist)
    blocklist = _normalize_list(config.access.group_blocklist)
    if normalized in blocklist:
        return False, f"群 {group_id} 位于黑名单"
    if allowlist and normalized not in allowlist:
        return False, f"群 {group_id} 不在白名单"
    return True, ""


def _redact_person_id(value: str | None, config: ContextBridgeToolConfig) -> str | None:
    """按隐私配置隐藏 person_id。"""

    if config.privacy.expose_person_id:
        return value
    return None


def _redact_message_id(value: str | None, config: ContextBridgeToolConfig) -> str | None:
    """按隐私配置隐藏 message_id。"""

    if config.privacy.expose_message_id:
        return value
    return None


def _redact_stream_id(value: str | None, config: ContextBridgeToolConfig) -> str | None:
    """按隐私配置隐藏 stream_id。"""

    if config.privacy.expose_stream_id:
        return value
    return None


def _stream_to_dict(stream: Any, config: ContextBridgeToolConfig) -> dict[str, Any]:
    """将 ChatStreams 记录转为安全字典。"""

    return {
        "stream_id": _redact_stream_id(str(getattr(stream, "stream_id", "") or ""), config),
        "platform": str(getattr(stream, "platform", "") or ""),
        "chat_type": str(getattr(stream, "chat_type", "") or ""),
        "group_id": getattr(stream, "group_id", None),
        "group_name": getattr(stream, "group_name", None),
        "last_active_time": getattr(stream, "last_active_time", None),
        "last_active_at": _format_time(getattr(stream, "last_active_time", None)),
        "created_at": _format_time(getattr(stream, "created_at", None)),
    }


def _message_to_dict(
    message: Any,
    *,
    max_chars: int,
    current_person_id: str,
    config: ContextBridgeToolConfig,
) -> dict[str, Any]:
    """将 Messages 记录转为模型友好的上下文字典。"""

    sender_person_id = getattr(message, "person_id", None)
    sender_id = str(getattr(message, "sender_id", "") or "")
    is_bot = bool(sender_id) and sender_id == str(getattr(message, "bot_id", "") or "")
    if is_bot:
        sender_role = "bot"
    elif sender_person_id == current_person_id:
        sender_role = "target_user"
    else:
        sender_role = "other"

    text = getattr(message, "processed_plain_text", None) or getattr(message, "content", "")
    return {
        "message_id": _redact_message_id(str(getattr(message, "message_id", "") or ""), config),
        "stream_id": _redact_stream_id(str(getattr(message, "stream_id", "") or ""), config),
        "person_id": _redact_person_id(sender_person_id, config),
        "sender_role": sender_role,
        "time": getattr(message, "time", None),
        "time_text": _format_time(getattr(message, "time", None)),
        "message_type": str(getattr(message, "message_type", "") or ""),
        "reply_to": _redact_message_id(getattr(message, "reply_to", None), config),
        "platform": getattr(message, "platform", None),
        "content": _content_preview(text, max_chars=max_chars),
    }


def _build_timeline(
    stream: Any,
    messages: list[dict[str, Any]],
    config: ContextBridgeToolConfig,
) -> str:
    """生成紧凑时间线文本，方便大模型直接阅读。"""

    stream_name = str(getattr(stream, "group_name", "") or getattr(stream, "stream_id", "") or "未知会话")
    lines = [f"【{getattr(stream, 'chat_type', '')}/{stream_name}】"]
    for item in messages:
        message_id = item.get("message_id") or "hidden"
        lines.append(
            f"[{item['time_text']}] {item['sender_role']} "
            f"({message_id}): {item['content']}"
        )
    return "\n".join(lines) if config.stream.include_timeline_text else ""


async def _resolve_user(
    *,
    platform: str,
    user_id: str,
    user_hint: str,
    config: ContextBridgeToolConfig,
) -> tuple[str, str, dict[str, Any] | None]:
    """解析目标用户，返回平台用户 ID、person_id 与用户信息。"""

    normalized_platform = _normalize_text(platform)
    normalized_user_id = _normalize_text(user_id)
    normalized_hint = _normalize_text(user_hint)

    if not normalized_platform:
        raise ValueError("platform 不能为空")

    ok, reason = _ensure_platform_allowed(config, normalized_platform)
    if not ok:
        raise ValueError(reason)

    helper = get_user_query_helper()
    resolved_user_id = normalized_user_id
    if not resolved_user_id and normalized_hint:
        resolved_user_id = await helper.resolve_user_id(normalized_platform, normalized_hint) or ""

    if not resolved_user_id and config.access.require_user_identity:
        raise ValueError("必须提供 user_id，或提供可唯一解析的 user_hint")
    if not resolved_user_id:
        resolved_user_id = normalized_hint

    person_id = helper.generate_person_id(normalized_platform, resolved_user_id)
    ok, reason = _ensure_user_allowed(
        config,
        platform=normalized_platform,
        user_id=resolved_user_id,
        person_id=person_id,
    )
    if not ok:
        raise ValueError(reason)

    person = await helper.person_crud.get_by(person_id=person_id)
    person_info = None
    if person is not None and config.privacy.include_person_profile:
        person_info = {
            "person_id": _redact_person_id(person_id, config),
            "platform": getattr(person, "platform", normalized_platform),
            "user_id": getattr(person, "user_id", resolved_user_id),
            "nickname": getattr(person, "nickname", None),
            "cardname": getattr(person, "cardname", None),
            "short_impression": getattr(person, "short_impression", None),
            "attitude": getattr(person, "attitude", None),
            "interaction_count": getattr(person, "interaction_count", None),
            "last_interaction": getattr(person, "last_interaction", None),
            "last_interaction_at": _format_time(getattr(person, "last_interaction", None)),
        }

    return resolved_user_id, person_id, person_info


async def _find_user_streams(
    *,
    person_id: str,
    platform: str,
    chat_scope: str,
    group_id: str,
    max_streams: int,
    config: ContextBridgeToolConfig,
) -> list[Any]:
    """查找与目标用户相关的私聊/群聊流。"""

    scope = _normalize_chat_scope(chat_scope, config)
    if not scope:
        return []

    normalized_group_id = _normalize_text(group_id)
    stream_rows: list[Any] = []

    if scope in {_ALL, _PRIVATE}:
        private_rows = await (
            QueryBuilder(ChatStreams)
            .filter(person_id=person_id, chat_type=_PRIVATE)
            .order_by("-last_active_time")
            .limit(max_streams)
            .all()
        )
        stream_rows.extend(private_rows)

    if scope in {_ALL, _GROUP}:
        group_stream_ids: list[str] = []

        if normalized_group_id:
            filters: dict[str, Any] = {"group_id": normalized_group_id, "chat_type": _GROUP}
            if _normalize_text(platform):
                filters["platform"] = platform
            explicit_group_rows = await (
                QueryBuilder(ChatStreams)
                .filter(**filters)
                .order_by("-last_active_time")
                .limit(max_streams)
                .all()
            )
            group_stream_ids.extend(
                str(getattr(row, "stream_id", "") or "")
                for row in explicit_group_rows
            )

        scan_limit = max_streams * config.limits.candidate_stream_scan_multiplier
        user_message_rows = await (
            QueryBuilder(Messages)
            .filter(person_id=person_id, platform=platform)
            .order_by("-time")
            .limit(scan_limit)
            .all()
        )
        candidate_ids = list(
            dict.fromkeys(
                str(getattr(row, "stream_id", "") or "")
                for row in user_message_rows
                if getattr(row, "stream_id", None)
            )
        )
        group_stream_ids.extend(candidate_ids)

        dedup_ids = [sid for sid in dict.fromkeys(group_stream_ids) if sid]
        if dedup_ids:
            group_rows = await (
                QueryBuilder(ChatStreams)
                .filter(stream_id__in=dedup_ids, chat_type=_GROUP)
                .order_by("-last_active_time")
                .limit(max_streams)
                .all()
            )
            stream_rows.extend(group_rows)

    dedup: dict[str, Any] = {}
    for stream in stream_rows:
        sid = str(getattr(stream, "stream_id", "") or "")
        if not sid:
            continue
        row_group_id = str(getattr(stream, "group_id", "") or "")
        ok, _ = _ensure_group_allowed(config, row_group_id)
        if not ok:
            continue
        if sid not in dedup:
            dedup[sid] = stream

    return sorted(
        dedup.values(),
        key=lambda item: float(getattr(item, "last_active_time", 0.0) or 0.0),
        reverse=True,
    )[:max_streams]


async def _get_messages_for_stream(
    *,
    stream_id: str,
    person_id: str,
    limit: int,
    around_user: bool,
) -> list[Any]:
    """从指定聊天流抓取消息。"""

    if not around_user:
        rows = await (
            QueryBuilder(Messages)
            .filter(stream_id=stream_id)
            .order_by("-time")
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    target_rows = await (
        QueryBuilder(Messages)
        .filter(stream_id=stream_id, person_id=person_id)
        .order_by("-time")
        .limit(max(1, limit // 2))
        .all()
    )
    if not target_rows:
        return []

    latest_target_time = max(float(getattr(row, "time", 0.0) or 0.0) for row in target_rows)
    rows = await (
        QueryBuilder(Messages)
        .filter(stream_id=stream_id, time__lte=latest_target_time)
        .order_by("-time")
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


class ContextMemoryLookupTool(BaseTool):
    """从长期记忆中检索用户相关内容。"""

    tool_name: str = "context_memory_lookup"
    tool_description: str = (
        "按平台用户检索长期记忆，用于私聊与群聊互通时了解同一用户的背景、事件、人物关系。"
        "当需要回忆该用户相关事实时优先调用。"
    )

    async def execute(
        self,
        platform: Annotated[str, "平台标识，例如 qq、wechat；用于定位用户身份"],
        user_id: Annotated[str, "目标用户的平台原始 ID；如果不知道可留空并填写 user_hint"] = "",
        user_hint: Annotated[str, "目标用户昵称、群名片或 @ 文本；仅在 user_id 为空时用于尝试唯一解析"] = "",
        query: Annotated[str, "检索关键词或问题；为空时按用户身份检索人物/事件记忆"] = "",
        top_n: Annotated[int, "最多返回的记忆数量；会受插件配置上限限制"] = 0,
        include_archived: Annotated[bool, "是否包含已归档/过期记忆；false 时使用插件默认值"] = False,
    ) -> tuple[bool, str | dict[str, Any]]:
        """检索 Booku Memory 中与目标用户相关的记忆。"""

        config = _get_config(self.plugin)
        try:
            resolved_user_id, person_id, person_info = await _resolve_user(
                platform=platform,
                user_id=user_id,
                user_hint=user_hint,
                config=config,
            )
        except ValueError as exc:
            return False, str(exc)

        default_top_n = config.limits.memory_top_n_default
        normalized_top_n = _clamp_int(
            top_n or default_top_n,
            minimum=1,
            maximum=max(1, config.limits.memory_top_n_max),
        )
        normalized_query = _normalize_text(query)
        should_include_archived = bool(include_archived or config.memory.include_archived_default)
        items: list[dict[str, Any]] = []
        memory_available = False
        errors: list[str] = []

        try:
            from plugins.booku_memory.service import BookuMemoryService

            service = BookuMemoryService(plugin=self.plugin)
            memory_available = True

            search_jobs = [
                {
                    "label": "person",
                    "kwargs": {
                        "top_n": normalized_top_n,
                        "query_text": normalized_query or None,
                        "memory_type": _MEMORY_PERSON_TYPE,
                        "person_id": person_id,
                        "include_archived": should_include_archived,
                        "include_knowledge": False,
                        "include_related": config.memory.include_related,
                    },
                },
                {
                    "label": "related",
                    "kwargs": {
                        "top_n": normalized_top_n,
                        "query_text": normalized_query or resolved_user_id,
                        "memory_type": None,
                        "person_id": person_id,
                        "include_archived": should_include_archived,
                        "include_knowledge": False,
                        "include_related": config.memory.include_related,
                    },
                },
            ]

            if normalized_query and config.memory.include_knowledge_when_query:
                search_jobs.append(
                    {
                        "label": "query",
                        "kwargs": {
                            "top_n": normalized_top_n,
                            "query_text": normalized_query,
                            "memory_type": None,
                            "person_id": None,
                            "include_archived": should_include_archived,
                            "include_knowledge": True,
                            "include_related": False,
                        },
                    }
                )

            seen_ids: set[str] = set()
            for job in search_jobs:
                result = await service.search_memory_entries(**job["kwargs"])
                for item in result.get("items", []) or []:
                    memory_id = str(item.get("id", "") or item.get("memory_id", "") or "")
                    if not memory_id or memory_id in seen_ids:
                        continue
                    seen_ids.add(memory_id)
                    payload = dict(item)
                    payload["matched_by"] = job["label"]
                    items.append(payload)
                    if len(items) >= normalized_top_n:
                        break
                if len(items) >= normalized_top_n:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Booku Memory 检索失败: {exc}", exc_info=True)
            errors.append(str(exc))

        return True, {
            "action": "context_memory_lookup",
            "ok": True,
            "memory_available": memory_available,
            "target": {
                "platform": _normalize_text(platform),
                "user_id": resolved_user_id,
                "person_id": _redact_person_id(person_id, config),
                "person_info": person_info,
            },
            "query": normalized_query,
            "total": len(items),
            "items": items,
            "errors": errors,
            "hint": "如果 total 为 0，可继续调用 context_stream_lookup 直接读取聊天流上下文。",
        }


class ContextStreamLookupTool(BaseTool):
    """从聊天流中抓取用户相关上下文。"""

    tool_name: str = "context_stream_lookup"
    tool_description: str = (
        "按平台用户从私聊与群聊聊天流中抓取近期上下文。"
        "当需要跨私聊/群聊了解同一用户说过什么、当前群聊背景或近期互动时调用。"
    )

    async def execute(
        self,
        platform: Annotated[str, "平台标识，例如 qq、wechat；用于定位用户身份"],
        user_id: Annotated[str, "目标用户的平台原始 ID；如果不知道可留空并填写 user_hint"] = "",
        user_hint: Annotated[str, "目标用户昵称、群名片或 @ 文本；仅在 user_id 为空时用于尝试唯一解析"] = "",
        chat_scope: Annotated[str, "读取范围：private 仅私聊、group 仅群聊、all 两者都读；受插件配置允许范围限制"] = _ALL,
        group_id: Annotated[str, "可选：限定某个群 ID；仅 group/all 范围有效，会受群黑白名单限制"] = "",
        per_stream_limit: Annotated[int, "每个聊天流最多返回的消息数；0 表示使用插件默认值"] = 0,
        max_streams: Annotated[int, "最多读取的聊天流数量；0 表示使用插件默认值"] = 0,
        around_user: Annotated[bool, "是否围绕该用户最近发言截取上下文；false 则取聊天流最近消息"] = True,
        max_chars_per_message: Annotated[int, "每条消息最多返回字符数；0 表示使用插件默认值"] = 0,
    ) -> tuple[bool, str | dict[str, Any]]:
        """抓取私聊/群聊聊天流上下文。"""

        started_at = time.time()
        config = _get_config(self.plugin)

        ok, reason = _ensure_group_allowed(config, group_id)
        if not ok:
            return False, reason

        try:
            resolved_user_id, person_id, person_info = await _resolve_user(
                platform=platform,
                user_id=user_id,
                user_hint=user_hint,
                config=config,
            )
        except ValueError as exc:
            return False, str(exc)

        normalized_platform = _normalize_text(platform)
        normalized_scope = _normalize_chat_scope(chat_scope, config)
        if not normalized_scope:
            return False, "当前配置未允许任何聊天范围"

        normalized_max_streams = _clamp_int(
            max_streams or config.limits.max_streams_default,
            minimum=1,
            maximum=max(1, config.limits.max_streams_max),
        )
        normalized_limit = _clamp_int(
            per_stream_limit or config.limits.per_stream_limit_default,
            minimum=1,
            maximum=max(1, config.limits.per_stream_limit_max),
        )
        normalized_chars = _clamp_int(
            max_chars_per_message or config.limits.max_chars_per_message_default,
            minimum=40,
            maximum=max(40, config.limits.max_chars_per_message_max),
        )
        effective_around_user = around_user if around_user is not None else config.stream.around_user_default

        streams = await _find_user_streams(
            person_id=person_id,
            platform=normalized_platform,
            chat_scope=normalized_scope,
            group_id=group_id,
            max_streams=normalized_max_streams,
            config=config,
        )

        stream_payloads: list[dict[str, Any]] = []
        for stream in streams:
            sid = str(getattr(stream, "stream_id", "") or "")
            if not sid:
                continue
            messages = await _get_messages_for_stream(
                stream_id=sid,
                person_id=person_id,
                limit=normalized_limit,
                around_user=effective_around_user,
            )
            message_items = [
                _message_to_dict(
                    message,
                    max_chars=normalized_chars,
                    current_person_id=person_id,
                    config=config,
                )
                for message in messages
            ]
            payload = {
                "stream": _stream_to_dict(stream, config),
                "message_count": len(message_items),
                "messages": message_items,
            }
            if config.stream.include_timeline_text:
                payload["timeline"] = _build_timeline(stream, message_items, config)
            stream_payloads.append(payload)

        return True, {
            "action": "context_stream_lookup",
            "ok": True,
            "target": {
                "platform": normalized_platform,
                "user_id": resolved_user_id,
                "person_id": _redact_person_id(person_id, config),
                "person_info": person_info,
            },
            "params": {
                "chat_scope": normalized_scope,
                "group_id": _normalize_text(group_id),
                "per_stream_limit": normalized_limit,
                "max_streams": normalized_max_streams,
                "around_user": effective_around_user,
            },
            "total_streams": len(stream_payloads),
            "streams": stream_payloads,
            "elapsed_seconds": round(time.time() - started_at, 3),
            "hint": "sender_role=target_user 表示目标用户，bot 表示机器人自身，other 表示上下文中的其他人。",
        }


__all__ = [
    "ContextMemoryLookupTool",
    "ContextStreamLookupTool",
]
