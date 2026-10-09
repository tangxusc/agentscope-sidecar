"""e2e 夹具的预期结果。

集中放在这里,改夹具时只改一处 —— 断言散在各测试文件里,加一个插件要翻
七个文件才找全。
"""

from __future__ import annotations

from agentscope_sidecar import PluginState

#: 主夹具 ``agent/plugins/`` 里每个插件的状态。
#:
#: ``scratch`` 有 ``_`` 前缀吗?没有 —— 它只是**没有 setup**,所以是 SKIPPED
#: 而不是被移出插件集。``_wip`` 才是被 ``_`` 前缀排除的那个。
MAIN_PLUGINS: dict[str, PluginState] = {
    "audit": PluginState.LOADED,
    "catalog": PluginState.LOADED,
    "consumer": PluginState.LOADED,
    "danger": PluginState.LOADED,
    "dashboard": PluginState.LOADED,
    "lifecycle": PluginState.LOADED,
    "notes": PluginState.LOADED,
    "permissions": PluginState.LOADED,
    "agent_tuning": PluginState.LOADED,
    "reports": PluginState.LOADED,
    "scratch": PluginState.SKIPPED,
    "slow": PluginState.LOADED,
    "weather": PluginState.LOADED,
}

#: 被 ``_`` 前缀移出插件集的条目 —— **不该出现在任何记录里**。
EXCLUDED: tuple[str, ...] = ("_wip",)

#: ``agent/faulty/`` 里的插件,id -> 错误消息里必须出现的关键词。
#:
#: 只断言 ``state == failed`` 是不够的 —— ``NameError`` 也会让插件 FAILED。
#: 每个 id 都要盯住它**该失败的原因**。值为 ``None`` 表示这个插件应当**加载成功**
#: (它失败的是别的东西 —— 比如 effect,或者它是先注册的那一个)。
FAULTY_PLUGINS: dict[str, str | None] = {
    "bad_return": "必须是 effect",
    "bad_route": "必须以 / 开头",
    "broken_effect": None,                       # effect 抛错不影响加载
    "broken_import": "ModuleNotFoundError",
    "broken_setup": "setup 故意炸了",
    "broken_signature": "必须是 async def",
    "dup_route_a": None,                         # 先注册的那个成功
    "dup_route_b": "路由冲突",
    "dup_service_a": None,                       # 先注册的那个成功
    "dup_service_b": "不能重复注册",
    "halfway": "装到一半炸了",
    "variadic": "*args",
}

#: 主夹具里的工具名(按插件归属)。
TOOLS_BY_PLUGIN: dict[str, tuple[str, ...]] = {
    "consumer": ("list_catalog",),
    "danger": ("wipe",),
    "notes": ("write_note",),
    "reports": ("make_report",),
    "slow": ("wait",),
    "weather": ("get_weather", "list_cities"),
}

#: 主夹具里的路由(method + path -> 归属)。
ROUTES_BY_PLUGIN: dict[str, str] = {
    "GET /": "dashboard",
    "GET /stats": "dashboard",
    "POST /stats": "dashboard",
}

#: 主夹具里的事件订阅数。
LIFECYCLE_EVENTS = 11

#: ``agent_tuning`` 改过的 Agent 构造参数。
TUNING_OVERRIDES: tuple[str, ...] = (
    "context_config",
    "injection_config",
    "react_config",
)

#: 主夹具里注册的服务(名 -> 归属)。
SERVICES_BY_PLUGIN: dict[str, str] = {
    "agent": "<base>",
    "catalog": "catalog",
    "commands": "<base>",
    "events": "<base>",
    "options": "<base>",
    "routes": "<base>",
    "telemetry": "<base>",
}
