"""agentscope-sidecar:运行时与插件分离的 Agent 底座。

插件只有一个入口 ``setup``,参数按签名注入(设计文档 §4 / §5)::

    from agentscope.agent import Agent
    from agentscope_sidecar import Effect

    async def setup(agent: Agent, options, ctx) -> Effect:
        ...
        return cleanup

**没有装饰器。** 工具、中间件、提示词、路由、命令都通过参数拿到的对象注册。
"""

from .app import Sidecar, boot_sidecar
from .context import BASE_OWNER, Context, ServiceRef
from .convention import CliOptions, RuntimeOptions
from .effects import Effect
from .errors import (
    ConventionError,
    DeclarationError,
    PluginImportError,
    ServiceNotFound,
    SidecarError,
)
from .loader import PluginLoader, PluginRecord, PluginState

__version__ = "0.2.0"

__all__ = [
    "BASE_OWNER",
    "CliOptions",
    "Context",
    "ConventionError",
    "DeclarationError",
    "Effect",
    "PluginImportError",
    "PluginLoader",
    "PluginRecord",
    "PluginState",
    "RuntimeOptions",
    "ServiceNotFound",
    "ServiceRef",
    "Sidecar",
    "SidecarError",
    "__version__",
    "boot_sidecar",
]
