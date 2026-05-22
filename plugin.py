"""Context Bridge Tool 插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .config import ContextBridgeToolConfig
from .event_handler import CrossStreamAutoInjector
from .tools import ContextMemoryLookupTool, ContextStreamLookupTool

logger = get_logger("context_bridge_tool")


@register_plugin
class ContextBridgeToolPlugin(BasePlugin):
    """私聊与群聊上下文互通工具插件。"""

    plugin_name: str = "context_bridge_tool"
    plugin_description: str = "私聊与群聊上下文互通工具插件，含自动跨流注入"
    plugin_version: str = "1.2.0-alpha"
    configs: list[type] = [ContextBridgeToolConfig]

    def get_components(self) -> list[type]:
        """返回插件组件列表。"""

        if isinstance(self.config, ContextBridgeToolConfig):
            if not self.config.plugin.enabled:
                logger.info("context_bridge_tool 已在配置中禁用")
                return []

            components: list[type] = []
            if self.config.components.enable_memory_lookup:
                components.append(ContextMemoryLookupTool)
            if self.config.components.enable_stream_lookup:
                components.append(ContextStreamLookupTool)
            if self.config.auto_inject.enabled:
                components.append(CrossStreamAutoInjector)
            return components

        # 配置不可用时仍返回所有组件，以避免因配置加载失败导致插件静默失效
        return [
            ContextMemoryLookupTool,
            ContextStreamLookupTool,
            CrossStreamAutoInjector,
        ]
