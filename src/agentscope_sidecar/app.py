"""底座主程序(设计文档 §3 / §11)。

时序:**解析选项 → 建容器 → 建 Agent → 加载插件(await setup)→ 应用过滤 → 跑**。

**Agent 在插件之前建好。** 插件拿到的是一个已经存在的 Agent,直接改它 —— 底座不
收集声明,也不按声明装配。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from agentscope.agent import Agent
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState
from agentscope.tool import Toolkit

from .context import BASE_OWNER, Context
from .convention import CliOptions, RuntimeOptions, is_interactive, resolve, resolve_model_ref
from .events import EventBus
from .loader import MIDDLEWARE_BUCKETS, PluginLoader, PluginRecord, PluginState
from .models import build_model
from .services import (
    AGENT_EVENT,
    CommandRegistry,
    RouteRegistry,
    Telemetry,
)
from .services.telemetry import LIFECYCLE_EVENTS

logger = logging.getLogger("sidecar")

BUILTIN_PLUGINS_DIR = Path(__file__).parent / "builtin_plugins"


class Sidecar:
    """底座实例。"""

    def __init__(
        self,
        options: RuntimeOptions,
        *,
        telemetry_sink: Path | None = None,
    ) -> None:
        self.options = options
        self.bus = EventBus()
        self.telemetry = Telemetry(telemetry_sink)
        self.ctx = Context(options, self.bus)
        self.routes = RouteRegistry()
        self.commands = CommandRegistry()
        self.loader = PluginLoader(
            options, self.ctx, self.bus,
            routes=self.routes, commands=self.commands,
        )

        self.model_ref: str | None = None
        self._agent: Any = None
        self._booted = False

    # ------------------------------------------------------------------ 启动

    def boot(self) -> None:
        """同步启动。内部用 ``asyncio.run`` —— 已在事件循环里时请用 ``aboot``。"""
        if self._booted:
            raise RuntimeError("底座已经启动")
        asyncio.run(self.aboot())

    async def aboot(self) -> None:
        """异步启动。供已经跑在事件循环里的宿主调用。"""
        if self._booted:
            raise RuntimeError("底座已经启动")
        self._booted = True

        _setup_logging(self.options.log_level)
        self.telemetry.attach(self.bus)
        self._register_base_services()

        self._agent = self._build_agent()
        self.ctx.provide("agent", self._agent)

        await self.bus.emit(
            "sidecar/boot",
            base_dir=str(self.options.base_dir),
            plugin_dirs=[str(p) for p in self.options.plugin_dirs],
        )

        await self.loader.load_all(
            self._agent,
            base_dirs=(BUILTIN_PLUGINS_DIR,),
            extensions=self.options.extensions,
        )
        for warning in self._warnings():
            logger.warning("%s", warning)

        await self._emit_plugin_states()
        self._apply_filters()
        await self.bus.emit("sidecar/ready", plugins=len(self.loader.records))

    def _register_base_services(self) -> None:
        for name, value in {
            "options": self.options,
            "events": self.bus,
            "telemetry": self.telemetry,
            "routes": self.routes,
            "commands": self.commands,
        }.items():
            self.ctx.provide(name, value)

    def _build_agent(self) -> Agent:
        """建默认 Agent。**空壳** —— 工具、中间件、提示词都由插件挂。"""
        self.model_ref = resolve_model_ref(self.options)
        return Agent(
            name="agent",
            system_prompt="",
            model=build_model(self.model_ref),
            toolkit=Toolkit(tools=[]),
            state=AgentState(permission_context=self._permission_context()),
        )

    def _permission_context(self) -> PermissionContext:
        """权限模式来自 ``--permission``;规则由插件往这个 context 上加。"""
        return PermissionContext(
            mode=PermissionMode(self.options.permission),
        )

    def _warnings(self) -> list[str]:
        result = self.loader.scan_result
        return list(result.warnings) if result else []

    async def _emit_plugin_states(self) -> None:
        """把当前全部插件记录的状态广播出去。启动与重载共用。"""
        for record in self.loader.all_records():
            if record.state is PluginState.LOADED:
                await self.bus.emit("sidecar/plugin-loaded", plugin=record.id,
                                    root=str(record.root))
            elif record.state is PluginState.SHADOWED:
                await self.bus.emit("sidecar/plugin-shadowed", plugin=record.id,
                                    shadowed=str(record.path),
                                    winner=str(record.shadowed_by))
            elif record.state is PluginState.FAILED:
                logger.error("插件 %s 激活失败:%s", record.id, record.error)
                await self.bus.emit("sidecar/plugin-failed", plugin=record.id,
                                    root=str(record.root), error=record.error or "")

    # ------------------------------------------------------------------ 过滤

    def _apply_filters(self) -> None:
        """应用 ``--tools`` / ``--middlewares``。

        必须在**所有 setup 之后** —— 工具与中间件是插件挂上的,之前它们还不存在。
        """
        if self.options.tools is not None:
            keep = set(self.options.tools)
            for group in self._agent.toolkit.tool_groups:
                group.tools = [t for t in group.tools if t.name in keep]
        if self.options.middlewares is not None:
            keep = set(self.options.middlewares)
            for bucket in MIDDLEWARE_BUCKETS:
                current = getattr(self._agent, bucket, None)
                if current is None:
                    continue
                setattr(
                    self._agent, bucket,
                    [mw for mw in current if type(mw).__name__ in keep],
                )

    # ------------------------------------------------------------------ 重载

    async def reload(self) -> None:
        """热重载:卸载全部插件 → 清模块缓存 → 重建 Agent → 重新加载。

        Agent 必须重建 —— 它持有旧插件贡献的中间件与工具实例。
        """
        self._agent = self._build_agent()
        self.ctx.provide("agent", self._agent)

        await self.loader.reload(
            self._agent,
            base_dirs=(BUILTIN_PLUGINS_DIR,),
            extensions=self.options.extensions,
        )
        for warning in self._warnings():
            logger.warning("%s", warning)

        await self._emit_plugin_states()
        self._apply_filters()
        await self.bus.emit("sidecar/reloaded", plugins=len(self.loader.records))

    # ------------------------------------------------------------------ 运行

    async def run(self) -> int:
        """按配置的运行模式执行。``--reload`` 时并行跑文件监听。"""
        watcher: asyncio.Task | None = None
        if self.options.reload:
            watcher = asyncio.create_task(self._watch_plugins())

        try:
            return await self._dispatch_mode()
        finally:
            if watcher is not None:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass

    async def _dispatch_mode(self) -> int:
        mode = self.options.mode
        if mode == "once":
            from .modes import once
            return await once.run(self)
        if mode == "console":
            from .modes import console
            return await console.run(self)
        if mode == "json":
            from .modes import json_mode
            return await json_mode.run(self)
        if mode == "rpc":
            from .modes import rpc
            return await rpc.run(self)
        if mode == "http":
            from .modes import http_mode
            return await http_mode.run(self)
        raise ValueError(f"未知运行模式:{mode!r}")

    async def _watch_plugins(self) -> None:
        """监听插件目录,有变化就重载。"""
        from .reload import watch

        dirs = [BUILTIN_PLUGINS_DIR, *self.options.plugin_dirs]

        async def on_change(changes: list[str]) -> None:
            logger.info("检测到插件变化(%s),开始重载", "; ".join(changes[:5]))
            try:
                await self.reload()
            except Exception:
                logger.exception("重载失败,保持原有插件继续运行")

        await watch(dirs, on_change)

    # ------------------------------------------------------------------ 装配

    def build_agent(self) -> Any:
        """返回当前 Agent。v2 里它在 boot 时就建好了。"""
        if self._agent is None:
            raise RuntimeError("底座未启动")
        return self._agent

    async def forward_event(self, event: Any) -> None:
        """把 agent 循环事件转发到总线。

        顺便在非交互模式下诊断「需要用户确认」—— 那时没人能回应,工具不会执行,
        整轮就此结束。不提示的话用户只会看到回答莫名其妙地短了(§11.8)。
        """
        event_type = _event_type(event)
        if event_type == "require_user_confirm" and not is_interactive(self.options.mode):
            names = [
                getattr(call, "name", "?")
                for call in (getattr(event, "tool_calls", None) or [])
            ]
            logger.warning(
                "工具 %s 需要用户确认,但 %s 模式无法交互 —— 本次调用已跳过。"
                "用 --permission accept_edits|dont_ask|bypass 调整策略。",
                names or "?", self.options.mode,
            )

        await self.bus.emit(
            AGENT_EVENT, type=event_type, summary=_summarize(event),
        )

    # ------------------------------------------------------------------ 关闭

    def shutdown(self) -> None:
        asyncio.run(self.ashutdown())

    async def ashutdown(self) -> None:
        """先广播 ``sidecar/shutdown``,再逆序调 effect。

        顺序不能反:插件要在**还接着线**的时候收到通知。反过来的话,effect 已经
        把订阅撤了(或把日志文件删了),紧接着的那条广播又会被残留的订阅者写一遍
        —— 关闭通知变成了「通知已关闭」的自相矛盾。
        """
        await self.bus.emit("sidecar/shutdown")
        await self.loader.shutdown()
        self.telemetry.close()

    # ------------------------------------------------------------------ 卸载

    async def unload(self, plugin_id: str) -> bool:
        """显式卸载一个插件。

        卸载完成后广播 ``sidecar/plugin-unloaded`` —— 没有这条,插件消失对
        外部观察者是完全静默的。
        """
        removed = await self.loader.unload(plugin_id)
        if removed:
            await self.bus.emit("sidecar/plugin-unloaded", plugin=plugin_id)
        return removed

    # ------------------------------------------------------------------ 报告

    def report(self) -> dict[str, Any]:
        agent = self._agent
        tools = _tool_names(agent) if agent else []
        return {
            "base_dir": str(self.options.base_dir),
            "plugin_dirs": [str(p) for p in self.options.plugin_dirs],
            "extensions": [str(p) for p in self.options.extensions],
            "model": self.model_ref,
            "mode": self.options.mode,
            "permission": self.options.permission,
            "plugins": self.loader.summary(),
            "warnings": self._warnings(),
            "tools": tools,
            "tool_owners": _tool_owners(self.loader, tools),
            "middlewares": _middleware_names(agent) if agent else [],
            "routes": self.routes.describe(),
            "commands": self.commands.describe(),
            "services": self.ctx.services(),
            "unresolved": sorted({
                name for r in self.loader.records.values()
                if r.state is PluginState.LOADED
                for name in r.unresolved
            }),
            "agent_overrides": _agent_overrides(self.loader),
            "telemetry": self.telemetry.snapshot(),
        }


def _tool_names(agent: Any) -> list[str]:
    return sorted(t.name for g in agent.toolkit.tool_groups for t in g.tools)


def _tool_owners(loader: PluginLoader, present: list[str]) -> dict[str, str]:
    """现存工具 -> 归属。

    只列 ``present`` 里的 —— ``--tools`` 过滤掉之后,``contributed`` 里仍然记着
    那些名字,但工具已经不在 Agent 上了。报告要描述**当前状态**,不然
    ``tools`` 和 ``tool_owners`` 两份数据自相矛盾。
    """
    known = set(present)
    out: dict[str, str] = {}
    for record in loader.records.values():
        for name in record.contributed.get("tools", []):
            if name in known:
                out.setdefault(name, record.id)
    return out


def _middleware_names(agent: Any) -> list[str]:
    names: list[str] = []
    for bucket in MIDDLEWARE_BUCKETS:
        names.extend(type(mw).__name__ for mw in getattr(agent, bucket, ()))
    return sorted(names)


def _agent_overrides(loader: PluginLoader) -> dict[str, str]:
    out: dict[str, str] = {}
    for record in loader.records.values():
        for field_name in record.contributed.get("agent_overrides", []):
            out.setdefault(field_name, record.id)
    return out


def boot_sidecar(
    cli: CliOptions | None = None,
    *,
    cwd: Path | None = None,
) -> Sidecar:
    """解析选项并启动底座。"""
    options = resolve(cli or CliOptions(), cwd=cwd)
    sidecar = Sidecar(options)
    sidecar.boot()
    return sidecar


def _event_type(event: Any) -> str:
    """事件名 —— 复用线格式的归一逻辑,不要再写第二份。

    这里曾经有一份独立实现,它不转小写(``EventType.REQUIRE_USER_CONFIRM.value``
    是大写的),导致「非交互模式下需要确认」的诊断判不中。
    """
    from .modes._wire import event_type

    return event_type(event)


def _summarize(event: Any) -> str:
    event_type = _event_type(event)
    if event_type == "tool_call_start":
        return f"tool_call:{getattr(event, 'tool_call_name', '?')}"
    if event_type == "model_call_start":
        return f"model:{getattr(event, 'model_name', '?')}"
    if event_type == "reply_end":
        return f"finished:{getattr(event, 'finished_reason', '?')}"
    return event_type


#: 底座自己装的日志 handler。重复 boot 时只摘自己那一个。
_LOG_HANDLER: logging.Handler | None = None


def _setup_logging(level: str) -> None:
    """日志一律走 stderr —— stdout 是协议通道。

    **只摘自己装的 handler**,不清空 root 的其余 handler。清空会把宿主的日志
    配置(包括 pytest 的 caplog)一并干掉 —— 一个库不该有这种副作用。
    """
    import sys

    global _LOG_HANDLER

    root = logging.getLogger()
    if _LOG_HANDLER is not None:
        root.removeHandler(_LOG_HANDLER)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s | %(message)s", datefmt="%H:%M:%S",
    ))
    root.addHandler(handler)
    _LOG_HANDLER = handler
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


__all__ = [
    "BASE_OWNER",
    "BUILTIN_PLUGINS_DIR",
    "LIFECYCLE_EVENTS",
    "PluginRecord",
    "PluginState",
    "Sidecar",
    "boot_sidecar",
]
