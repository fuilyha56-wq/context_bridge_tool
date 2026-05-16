"""跨流上下文自动注入事件处理器。

监听 on_prompt_build 事件，当 KFC（私聊）或 default_chatter（群聊）
构建 user prompt 时，自动查询该用户在另一侧聊天流的近期消息，
统一通过 values.extra 注入，使 LLM 在决策时能看到跨流上下文，
避免 send_to 发消息时上下文割裂。

KFC 模式下，plugin_source.py 会自动将 values.extra 中的 legacy 文本
归一化为 ContextContribution(notice/turn)，功能与直接使用
context_contributions 等效，同时避免向 params 顶层添加新 key
导致 EventBus next_params 签名不一致校验失败。
"""

from __future__ import annotations

import time
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseEventHandler
from src.core.models.sql_alchemy import ChatStreams, Messages
from src.core.utils.user_query_helper import get_user_query_helper
from src.kernel.db import QueryBuilder
from src.kernel.event import EventDecision

from .config import ContextBridgeToolConfig

logger = get_logger("context_bridge_tool.event_handler")

# 需要拦截的 prompt 模板名
_KFC_USER_PROMPT = "kfc_user_prompt"
_DEFAULT_CHATTER_USER_PROMPT = "default_chatter_user_prompt"
_TARGET_PROMPTS = {_KFC_USER_PROMPT, _DEFAULT_CHATTER_USER_PROMPT}


def _get_config(plugin: Any) -> ContextBridgeToolConfig:
    """从插件实例读取配置，失败时回退默认配置。"""
    config = getattr(plugin, "config", None)
    if isinstance(config, ContextBridgeToolConfig):
        return config
    return ContextBridgeToolConfig()


def _normalize_text(value: str | None) -> str:
    return str(value or "").strip()


def _format_time(value: Any) -> str:
    """格式化 Unix 时间戳。"""
    from datetime import datetime
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value or "")


def _content_preview(value: Any, max_chars: int) -> str:
    """生成消息正文预览。"""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[:max(0, max_chars - 1)] + "…"


async def _resolve_cross_streams(
    *,
    current_stream_id: str,
    platform: str,
    person_id: str,
    config: ContextBridgeToolConfig,
) -> list[dict[str, Any]]:
    """查询当前用户在其他聊天流的近期消息摘要。

    查询策略：
    - 私聊→查群聊 + 同平台其他私聊
    - 群聊→查该用户的私聊
    排除当前流本身，按活跃度排序。
    """
    # 确定当前流类型
    current_rows = await (
        QueryBuilder(ChatStreams)
        .filter(stream_id=current_stream_id)
        .limit(1)
        .all()
    )
    if not current_rows:
        return []

    current_stream = current_rows[0]
    current_chat_type = str(getattr(current_stream, "chat_type", "") or "")

    max_streams = config.auto_inject.max_streams
    candidate_streams: list[Any] = []

    if current_chat_type == "private":
        # 私聊→查群聊 + 其他私聊
        # 1. 该用户的群聊（通过消息记录找活跃群）
        scan_limit = max_streams * 20
        user_msg_rows = await (
            QueryBuilder(Messages)
            .filter(person_id=person_id, platform=platform)
            .order_by("-time")
            .limit(scan_limit)
            .all()
        )
        candidate_stream_ids = list(
            dict.fromkeys(
                str(getattr(row, "stream_id", "") or "")
                for row in user_msg_rows
                if getattr(row, "stream_id", None)
            )
        )
        if candidate_stream_ids:
            group_rows = await (
                QueryBuilder(ChatStreams)
                .filter(stream_id__in=candidate_stream_ids, chat_type="group", platform=platform)
                .order_by("-last_active_time")
                .limit(max_streams)
                .all()
            )
            candidate_streams.extend(group_rows)

        # 2. 同平台其他私聊（不同 person_id，即不同对话对象）
        other_private_rows = await (
            QueryBuilder(ChatStreams)
            .filter(chat_type="private", platform=platform)
            .order_by("-last_active_time")
            .limit(max_streams + 5)  # 多取几条，排除当前流后仍有余量
            .all()
        )
        for row in other_private_rows:
            sid = str(getattr(row, "stream_id", "") or "")
            if sid != current_stream_id:
                candidate_streams.append(row)

    elif current_chat_type == "group":
        # 群聊→查该用户的私聊
        private_rows = await (
            QueryBuilder(ChatStreams)
            .filter(person_id=person_id, chat_type="private", platform=platform)
            .order_by("-last_active_time")
            .limit(max_streams)
            .all()
        )
        candidate_streams.extend(private_rows)
    else:
        return []

    if not candidate_streams:
        return []

    # 去重并排除当前流，按活跃时间排序
    seen_sids: set[str] = set()
    deduped: list[Any] = []
    for stream in candidate_streams:
        sid = str(getattr(stream, "stream_id", "") or "")
        if not sid or sid == current_stream_id or sid in seen_sids:
            continue
        seen_sids.add(sid)
        deduped.append(stream)

    deduped.sort(
        key=lambda s: float(getattr(s, "last_active_time", 0.0) or 0.0),
        reverse=True,
    )
    deduped = deduped[:max_streams]

    # 从每个流抓取近期消息
    results: list[dict[str, Any]] = []
    max_chars = config.auto_inject.max_chars_per_message
    per_limit = config.auto_inject.per_stream_limit

    for stream in deduped:
        sid = str(getattr(stream, "stream_id", "") or "")
        if not sid or sid == current_stream_id:
            continue

        stream_name = str(
            getattr(stream, "group_name", "")
            or getattr(stream, "partner_name", "")
            or getattr(stream, "stream_id", "")
            or "未知"
        )
        chat_type = str(getattr(stream, "chat_type", "") or "")

        msg_rows = await (
            QueryBuilder(Messages)
            .filter(stream_id=sid)
            .order_by("-time")
            .limit(per_limit)
            .all()
        )

        if not msg_rows:
            continue

        timeline_lines: list[str] = []
        for msg in reversed(msg_rows):  # 按时间正序
            msg_time = _format_time(getattr(msg, "time", None))
            sender_id = str(getattr(msg, "sender_id", "") or "")
            bot_id = str(getattr(current_stream, "bot_id", "") or "")
            is_bot = bool(bot_id and sender_id == bot_id)

            content_text = _content_preview(
                getattr(msg, "processed_plain_text", None) or getattr(msg, "content", ""),
                max_chars=max_chars,
            )
            if not content_text:
                continue

            if is_bot:
                timeline_lines.append(f"[{msg_time}] bot: {content_text}")
            else:
                sender_name = str(getattr(msg, "sender_name", "对方") or "对方")
                timeline_lines.append(f"[{msg_time}] {sender_name}: {content_text}")

        if timeline_lines:
            scope_label = "群聊" if chat_type == "group" else "私聊"
            results.append({
                "stream_name": stream_name,
                "chat_type": chat_type,
                "scope_label": scope_label,
                "timeline": "\n".join(timeline_lines),
            })

    return results


def _build_injection_text(
    cross_streams: list[dict[str, Any]],
    *,
    is_kfc: bool,
) -> str:
    """构建注入文本。

    KFC 模式：生成 ContextContribution 格式的 notice 文本。
    default_chatter 模式：生成 extra 文本。
    """
    if not cross_streams:
        return ""

    parts: list[str] = []
    for stream_info in cross_streams:
        scope_label = stream_info["scope_label"]
        stream_name = stream_info["stream_name"]
        timeline = stream_info["timeline"]
        parts.append(f"【{scope_label}：{stream_name}】\n{timeline}")

    body = "\n\n".join(parts)

    if is_kfc:
        return (
            "## 跨流上下文\n"
            "以下是对方在其他聊天流中的近期对话，供你参考：\n\n"
            f"{body}\n\n"
            "- 这是对方在其他会话中的对话记录，你可以据此理解对方当前的话题和状态，"
            "但不要直接提及或引用这些内容，除非对方主动提起。"
        )
    else:
        return (
            "## 跨流上下文\n"
            "以下是对方在其他聊天流中的近期对话，供你参考：\n\n"
            f"{body}\n\n"
            "- 这是对方在其他会话中的对话记录，供你了解对方当前的话题和状态。"
        )


class CrossStreamAutoInjector(BaseEventHandler):
    """跨流上下文自动注入器。

    监听 on_prompt_build 事件，当 KFC 或 default_chatter
    构建 user prompt 时，自动查询该用户在另一侧聊天流的近期消息，
    并注入到 prompt 中，使 LLM 能看到跨流上下文。
    """

    handler_name: str = "cross_stream_auto_injector"
    handler_description: str = "在 prompt 构建时自动注入跨流上下文，使 LLM 能看到另一侧的近期对话"
    weight: int = 5
    intercept_message: bool = False
    init_subscribe: list[str] = ["on_prompt_build"]

    _recent_queries: dict[str, float]  # stream_id -> last_query_time

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._recent_queries = {}

    def _get_cooldown_seconds(self) -> int:
        """从配置读取冷却秒数。"""
        config = _get_config(self.plugin)
        return int(config.auto_inject.cooldown_seconds)

    def _prune_cooldown(self, now: float) -> None:
        """清理过期的冷却记录。"""
        cooldown = self._get_cooldown_seconds()
        expired = [
            sid for sid, ts in self._recent_queries.items()
            if now - ts >= cooldown
        ]
        for sid in expired:
            self._recent_queries.pop(sid, None)

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 on_prompt_build 事件，自动注入跨流上下文。"""
        prompt_name = params.get("name", "")
        if prompt_name not in _TARGET_PROMPTS:
            return EventDecision.SUCCESS, params

        config = _get_config(self.plugin)
        if not config.auto_inject.enabled:
            return EventDecision.SUCCESS, params

        # 从 values 中提取 stream_id 和平台信息
        values: dict[str, Any] = params.get("values", {})
        stream_id = _normalize_text(values.get("stream_id", ""))

        # 冷却检查
        now = time.time()
        self._prune_cooldown(now)
        cooldown = self._get_cooldown_seconds()
        if stream_id and stream_id in self._recent_queries:
            if now - self._recent_queries[stream_id] < cooldown:
                return EventDecision.SUCCESS, params

        if not stream_id:
            return EventDecision.SUCCESS, params

        # 解析当前流的平台和 person_id
        current_rows = await (
            QueryBuilder(ChatStreams)
            .filter(stream_id=stream_id)
            .limit(1)
            .all()
        )
        if not current_rows:
            return EventDecision.SUCCESS, params

        current_stream = current_rows[0]
        platform = str(getattr(current_stream, "platform", "") or "")
        chat_type = str(getattr(current_stream, "chat_type", "") or "")
        person_id = str(getattr(current_stream, "person_id", "") or "")

        # 群聊的 ChatStreams.person_id 可能并非真正用户 ID，
        # 需要从该流最近的消息中获取触发本次 prompt 的用户
        if not person_id and chat_type == "group":
            recent_msgs = await (
                QueryBuilder(Messages)
                .filter(stream_id=stream_id, platform=platform)
                .order_by("-time")
                .limit(5)
                .all()
            )
            for msg in recent_msgs:
                msg_person_id = getattr(msg, "person_id", None)
                msg_sender_id = str(getattr(msg, "sender_id", "") or "")
                bot_id = str(getattr(current_stream, "bot_id", "") or "")
                if msg_person_id and msg_sender_id != bot_id:
                    person_id = str(msg_person_id)
                    break

        if not platform or not person_id:
            return EventDecision.SUCCESS, params

        # 判断是否为 KFC
        is_kfc = prompt_name == _KFC_USER_PROMPT

        try:
            cross_streams = await _resolve_cross_streams(
                current_stream_id=stream_id,
                platform=platform,
                person_id=person_id,
                config=config,
            )
        except Exception as exc:
            logger.warning(f"跨流上下文查询失败: {exc}")
            return EventDecision.SUCCESS, params

        if not cross_streams:
            return EventDecision.SUCCESS, params

        # 记录冷却
        if stream_id:
            self._recent_queries[stream_id] = now

        injection_text = _build_injection_text(cross_streams, is_kfc=is_kfc)

        # KFC 和 default_chatter 统一通过 values.extra 注入
        # 注意：不能向 params 顶层添加新 key（如 context_contributions），
        # 否则 EventBus next_params 签名不一致校验会丢弃整个处理器的影响。
        # KFC 的 plugin_source.py 会自动将 values.extra 中的 legacy 文本
        # 归一化为 ContextContribution(notice/turn)，功能等效。
        existing_extra: str = values.get("extra", "") or ""
        separator = "\n\n" if existing_extra else ""
        values["extra"] = existing_extra + separator + injection_text
        params["values"] = values

        stream_count = len(cross_streams)
        logger.info(
            f"已注入跨流上下文: prompt={prompt_name}, "
            f"stream_id={stream_id}, streams={stream_count}"
        )
        return EventDecision.SUCCESS, params
