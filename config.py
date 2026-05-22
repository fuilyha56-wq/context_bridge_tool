"""Context Bridge Tool 插件配置。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class ContextBridgeToolConfig(BaseConfig):
    """私聊/群聊上下文互通工具配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "Context Bridge Tool 配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件级开关。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 Context Bridge Tool 插件",
            label="启用插件",
            tag="plugin",
        )
        version: str = Field(
            default="1.2.0-alpha",
            description="插件版本",
            label="插件版本",
            disabled=True,
            tag="general",
        )

    @config_section("components", title="组件开关", tag="plugin")
    class ComponentsSection(SectionBase):
        """两个 Tool 的独立启用配置。"""

        enable_memory_lookup: bool = Field(
            default=True,
            description="是否启用 context_memory_lookup 长期记忆检索工具",
            label="启用记忆检索工具",
            tag="plugin",
        )
        enable_stream_lookup: bool = Field(
            default=True,
            description="是否启用 context_stream_lookup 聊天流上下文工具",
            label="启用聊天流工具",
            tag="plugin",
        )

    @config_section("access", title="访问控制", tag="security")
    class AccessSection(SectionBase):
        """工具暴露范围与用户访问控制。"""

        allowed_chat_scopes: list[str] = Field(
            default_factory=lambda: ["private", "group", "all"],
            description="允许模型请求的聊天范围，可选 private/group/all",
            label="允许聊天范围",
            input_type="list",
            item_type="str",
            tag="security",
        )
        platform_allowlist: list[str] = Field(
            default_factory=list,
            description="允许查询的平台白名单；留空表示不限制",
            label="平台白名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        platform_blocklist: list[str] = Field(
            default_factory=list,
            description="禁止查询的平台黑名单，优先级高于白名单",
            label="平台黑名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        user_allowlist: list[str] = Field(
            default_factory=list,
            description="允许查询的用户白名单；支持 user_id、platform:user_id、person_id；留空表示不限制",
            label="用户白名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        user_blocklist: list[str] = Field(
            default_factory=list,
            description="禁止查询的用户黑名单；支持 user_id、platform:user_id、person_id，优先级高于白名单",
            label="用户黑名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        group_allowlist: list[str] = Field(
            default_factory=list,
            description="允许查询的群 ID 白名单；留空表示不限制",
            label="群白名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        group_blocklist: list[str] = Field(
            default_factory=list,
            description="禁止查询的群 ID 黑名单，优先级高于白名单",
            label="群黑名单",
            input_type="list",
            item_type="str",
            tag="security",
        )
        require_user_identity: bool = Field(
            default=True,
            description="是否要求必须定位到明确用户；关闭后仍建议模型传 user_id",
            label="要求明确用户身份",
            tag="security",
        )

    @config_section("limits", title="返回限制", tag="performance")
    class LimitsSection(SectionBase):
        """避免工具返回过大上下文。"""

        memory_top_n_default: int = Field(
            default=8,
            description="记忆检索默认返回条数",
            label="默认记忆条数",
            ge=1,
            le=20,
            tag="performance",
        )
        memory_top_n_max: int = Field(
            default=20,
            description="记忆检索最大返回条数",
            label="最大记忆条数",
            ge=1,
            le=50,
            tag="performance",
        )
        per_stream_limit_default: int = Field(
            default=20,
            description="聊天流工具每个流默认返回消息数",
            label="默认每流消息数",
            ge=1,
            le=80,
            tag="performance",
        )
        per_stream_limit_max: int = Field(
            default=80,
            description="聊天流工具每个流最大返回消息数",
            label="最大每流消息数",
            ge=1,
            le=200,
            tag="performance",
        )
        max_streams_default: int = Field(
            default=4,
            description="聊天流工具默认读取的流数量",
            label="默认流数量",
            ge=1,
            le=10,
            tag="performance",
        )
        max_streams_max: int = Field(
            default=10,
            description="聊天流工具最大读取的流数量",
            label="最大流数量",
            ge=1,
            le=30,
            tag="performance",
        )
        max_chars_per_message_default: int = Field(
            default=300,
            description="每条消息默认最大返回字符数",
            label="默认消息长度",
            ge=40,
            le=1000,
            tag="text",
        )
        max_chars_per_message_max: int = Field(
            default=1000,
            description="每条消息最大返回字符数",
            label="最大消息长度",
            ge=40,
            le=3000,
            tag="text",
        )
        candidate_stream_scan_multiplier: int = Field(
            default=80,
            description="按目标用户消息回溯群聊候选流时的扫描倍数",
            label="候选流扫描倍数",
            ge=10,
            le=300,
            tag="performance",
        )

    @config_section("privacy", title="隐私与脱敏", tag="security")
    class PrivacySection(SectionBase):
        """控制工具结果中暴露的字段。"""

        expose_person_id: bool = Field(
            default=True,
            description="是否在结果中暴露内部 person_id；关闭后会隐藏该字段",
            label="暴露 person_id",
            tag="security",
        )
        expose_message_id: bool = Field(
            default=True,
            description="是否在结果中暴露 message_id；关闭后会隐藏该字段",
            label="暴露 message_id",
            tag="security",
        )
        expose_stream_id: bool = Field(
            default=True,
            description="是否在结果中暴露 stream_id；关闭后会隐藏该字段",
            label="暴露 stream_id",
            tag="security",
        )
        include_person_profile: bool = Field(
            default=True,
            description="是否返回用户画像字段，例如昵称、备注、短印象、态度等",
            label="返回用户画像",
            tag="security",
        )

    @config_section("memory", title="记忆检索策略", tag="ai")
    class MemorySection(SectionBase):
        """长期记忆检索策略。"""

        include_archived_default: bool = Field(
            default=False,
            description="模型未显式指定时是否默认包含归档记忆",
            label="默认包含归档",
            tag="general",
        )
        include_knowledge_when_query: bool = Field(
            default=True,
            description="有 query 时是否允许同时检索知识类记忆",
            label="有查询时包含知识库",
            tag="ai",
        )
        include_related: bool = Field(
            default=True,
            description="是否检索关联记忆",
            label="包含关联记忆",
            tag="ai",
        )

    @config_section("stream", title="聊天流策略", tag="general")
    class StreamSection(SectionBase):
        """聊天流抓取策略。"""

        around_user_default: bool = Field(
            default=True,
            description="默认围绕目标用户最近发言截取上下文",
            label="默认围绕用户截取",
            tag="general",
        )
        include_timeline_text: bool = Field(
            default=True,
            description="是否额外返回适合模型直接阅读的 timeline 文本",
            label="返回时间线文本",
            tag="text",
        )

    @config_section("auto_inject", title="跨流自动注入", tag="ai")
    class AutoInjectSection(SectionBase):
        """跨流上下文自动注入配置。

        启用后，在指定的 prompt 构建时，
        自动查询该用户在另一侧聊天流的近期消息并注入上下文，
        使 LLM 在决策时能看到跨流上下文。
        """

        enabled: bool = Field(
            default=True,
            description="是否启用跨流上下文自动注入",
            label="启用自动注入",
            tag="ai",
        )
        target_prompts: list[str] = Field(
            default_factory=lambda: ["kfc_user_prompt", "default_chatter_user_prompt"],
            description="需要自动注入跨流上下文的 prompt 模板名称列表",
            label="目标 Prompt 列表",
            input_type="list",
            item_type="str",
            tag="ai",
        )
        kfc_prompts: list[str] = Field(
            default_factory=lambda: ["kfc_user_prompt"],
            description="使用 KFC 格式注入的 prompt 名称列表（需同时在 target_prompts 中）",
            label="KFC 格式 Prompt",
            input_type="list",
            item_type="str",
            tag="ai",
        )
        per_stream_limit: int = Field(
            default=10,
            description="自动注入时每个聊天流最多抓取的消息数",
            label="每流消息数",
            ge=1,
            le=40,
            tag="performance",
        )
        max_streams: int = Field(
            default=2,
            description="自动注入时最多读取的跨流数量",
            label="最大跨流数",
            ge=1,
            le=6,
            tag="performance",
        )
        max_chars_per_message: int = Field(
            default=200,
            description="自动注入时每条消息最大字符数",
            label="消息长度限制",
            ge=40,
            le=500,
            tag="text",
        )
        cooldown_seconds: int = Field(
            default=30,
            description="同一聊天流两次注入之间的冷却秒数",
            label="注入冷却秒数",
            ge=5,
            le=300,
            tag="performance",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    components: ComponentsSection = Field(default_factory=ComponentsSection)
    access: AccessSection = Field(default_factory=AccessSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)
    privacy: PrivacySection = Field(default_factory=PrivacySection)
    memory: MemorySection = Field(default_factory=MemorySection)
    stream: StreamSection = Field(default_factory=StreamSection)
    auto_inject: AutoInjectSection = Field(default_factory=AutoInjectSection)
