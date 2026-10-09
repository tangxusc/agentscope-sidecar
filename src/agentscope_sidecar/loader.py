"""加载器(设计文档 §3 / §8)。

时序:**导入模块 → 建 Agent → await setup(签名注入)→ 收 effect**。
没有声明收集,没有旁路通道,没有依赖排序 —— 依赖靠签名注入(§5)。
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

from .context import BASE_OWNER, Context, owned_by
from .discover import (
    MODULE_PREFIX,
    PluginSource,
    ScanResult,
    invalid_name_warning,
    import_module,
    module_key,
    scan,
    valid_plugin_name,
)
from .effects import Effect, run_effect
from .events import EventBus
from .inject import resolve_arguments

logger = logging.getLogger("sidecar.loader")

#: Agent 上按 hook 分好的七个中间件桶(§5.4)。
MIDDLEWARE_BUCKETS = (
    "_acting_middlewares",
    "_model_call_middlewares",
    "_reasoning_middlewares",
    "_reply_middlewares",
    "_system_prompt_middlewares",
    "_check_permission_middlewares",
    "_compress_context_middlewares",
)

#: 会被插件改写的 Agent 构造参数(§8.1)。按对象身份判定谁改过。
OVERRIDE_FIELDS = (
    "context_config",
    "model_config",
    "injection_config",
    "react_config",
    "offloader",
)


class PluginState(str, Enum):
    LOADED = "loaded"
    FAILED = "failed"
    SKIPPED = "skipped"
    SHADOWED = "shadowed"
    UNLOADED = "unloaded"


@dataclass
class PluginRecord:
    id: str
    path: Path
    root: Path
    state: PluginState
    error: str | None = None
    shadowed_by: Path | None = None
    ctx: Context | None = None
    effect: Effect | None = None
    #: 快照差分的结果:{"tools": [...], "middlewares": [...]}(§8.1)
    contributed: dict[str, list[str]] = field(default_factory=dict)
    #: 拿到惰性代理的参数名(§5.2)
    unresolved: list[str] = field(default_factory=list)
    module_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
            "root": str(self.root),
            "path": str(self.path),
            "error": self.error,
            "shadowed_by": str(self.shadowed_by) if self.shadowed_by else None,
            "contributed": {k: list(v) for k, v in self.contributed.items()},
            "unresolved": list(self.unresolved),
        }


@dataclass(frozen=True)
class _Snapshot:
    """Agent 上一个时间点的可观测状态。"""

    tools: dict[str, int]
    middlewares: dict[str, int]
    #: 存**对象本身**而不是 ``id()``。只存 id 的话旧对象可能被回收,新对象
    #: 恰好复用同一个地址 —— 那时「换过对象」会被判成「没换」。
    overrides: dict[str, Any]

    def diff(self, before: "_Snapshot") -> dict[str, list[str]]:
        """相对 ``before`` 新增了什么。"""
        out: dict[str, list[str]] = {}
        for kind in ("tools", "middlewares"):
            after_map: dict[str, int] = getattr(self, kind)
            before_map: dict[str, int] = getattr(before, kind)
            added: list[str] = []
            for name, count in after_map.items():
                added.extend([name] * (count - before_map.get(name, 0)))
            if added:
                out[kind] = sorted(added)
        return out

    def changed_overrides(self, before: "_Snapshot") -> list[str]:
        """哪些 Agent 构造参数被换过对象。"""
        return sorted(
            name for name, value in self.overrides.items()
            if before.overrides.get(name) is not value
        )


def snapshot(agent: Any) -> _Snapshot:
    """读 Agent 当前状态。**同步** —— 直接看 ``tool_groups`` 里的列表。"""
    tools: Counter[str] = Counter()
    for group in agent.toolkit.tool_groups:
        for tool in group.tools:
            tools[tool.name] += 1

    middlewares: Counter[str] = Counter()
    for bucket in MIDDLEWARE_BUCKETS:
        for mw in getattr(agent, bucket, ()):
            middlewares[type(mw).__name__] += 1

    overrides = {
        name: getattr(agent, name, None) for name in OVERRIDE_FIELDS
    }
    return _Snapshot(dict(tools), dict(middlewares), overrides)


class PluginLoader:
    """插件加载器。"""

    def __init__(
        self,
        options: Any,
        ctx: Context,
        bus: EventBus,
        *,
        routes: Any = None,
        commands: Any = None,
    ) -> None:
        self.options = options
        self.ctx = ctx
        self.bus = bus
        #: 路由与命令不在 Agent 上,回滚时要单独处理。允许为 None(纯容器测试)。
        self.routes = routes
        self.commands = commands
        #: 生效的插件,按 id 唯一
        self.records: dict[str, PluginRecord] = {}
        #: 被覆盖的插件。同一个 id 可以有多条(三层同名时两条都被覆盖)
        self.shadowed_records: list[PluginRecord] = []
        self.scan_result: ScanResult | None = None
        #: 按加载顺序记 effect,退出时逆序调用
        self._effects: list[tuple[str, Effect]] = []
        self._modules: dict[str, ModuleType] = {}
        #: 当前 Agent。回滚时要知道从哪个对象上摘工具。
        self._agent: Any = None

    def all_records(self) -> list[PluginRecord]:
        """生效的 + 被覆盖的全部记录。"""
        return [*self.records.values(), *self.shadowed_records]

    # ------------------------------------------------------------------ 加载

    async def load_all(
        self,
        agent: Any,
        *,
        base_dirs: Sequence[Path] = (),
        extensions: Sequence[Path] = (),
    ) -> list[PluginRecord]:
        """扫描并激活全部插件。

        优先级从低到高:``base_dirs``(底座自带)→ ``options.plugin_dirs``
        → ``extensions``(``-e`` 临时文件,最高)。

        ``base_dirs`` 必须排在**最前** —— 底座自带的插件要能被用户插件覆盖。
        """
        self._agent = agent
        plugin_dirs = [*base_dirs, *self.options.plugin_dirs]
        result = scan(plugin_dirs)
        _apply_extensions(result, extensions, start_order=len(plugin_dirs))
        self.scan_result = result

        for loser, winner in result.shadowed:
            self.shadowed_records.append(PluginRecord(
                id=loser.plugin_id, path=loser.path, root=loser.root,
                state=PluginState.SHADOWED, shadowed_by=winner.path,
            ))

        for source in result.sources:
            self.records[source.plugin_id] = await self._activate(source, agent)

        return list(self.records.values())

    async def _activate(self, source: PluginSource, agent: Any) -> PluginRecord:
        base = dict(id=source.plugin_id, path=source.path, root=source.root)

        try:
            module = import_module(source)
        except Exception as exc:
            return PluginRecord(**base, state=PluginState.FAILED,
                                error=f"{type(exc).__name__}: {exc}")

        self._modules[module_key(source.plugin_id)] = module

        setup = getattr(module, "setup", None)
        if setup is None:
            return PluginRecord(**base, state=PluginState.SKIPPED,
                                module_name=module.__name__)
        if not callable(setup):
            return PluginRecord(
                **base, state=PluginState.FAILED, module_name=module.__name__,
                error=f"setup 不是可调用的:{type(setup)!r}",
            )
        if not _is_async(setup):
            return PluginRecord(
                **base, state=PluginState.FAILED, module_name=module.__name__,
                error="setup 必须是 async def —— AgentScope 的 Toolkit API 是异步的",
            )

        plugin_ctx = self.ctx.for_plugin(
            source.plugin_id, source.plugin_dir, source.root,
        )

        before = snapshot(agent)
        try:
            kwargs, unresolved = resolve_arguments(setup, plugin_ctx)
            # 路由、命令这类不在 Agent 上的注册靠 current_owner() 记归属(§8.1)
            with owned_by(source.plugin_id):
                effect = await setup(**kwargs)
        except Exception as exc:
            self._rollback(agent, source.plugin_id, snapshot(agent).diff(before))
            return PluginRecord(
                **base, state=PluginState.FAILED, module_name=module.__name__,
                error=f"{type(exc).__name__}: {exc}",
            )

        after = snapshot(agent)
        contributed = after.diff(before)

        if effect is not None and not callable(effect):
            self._rollback(agent, source.plugin_id, contributed)
            return PluginRecord(
                **base, state=PluginState.FAILED, module_name=module.__name__,
                error=f"setup 的返回值必须是 effect 或 None,收到 {type(effect)!r}",
            )

        for field_name in after.changed_overrides(before):
            contributed.setdefault("agent_overrides", []).append(field_name)

        if effect is not None:
            self._effects.append((source.plugin_id, effect))

        return PluginRecord(
            **base, state=PluginState.LOADED, ctx=plugin_ctx, effect=effect,
            contributed=contributed, unresolved=unresolved,
            module_name=module.__name__,
        )

    # ------------------------------------------------------------------ 回滚

    def _rollback(
        self, agent: Any, plugin_id: str, contributed: dict[str, list[str]],
    ) -> None:
        """撤销一个插件在 Agent 与各注册表上留下的痕迹。

        **``setup`` 半途抛异常时必须调它。** 否则插件的状态是 ``failed``,但它
        已经挂上的工具、路由、命令、服务全都还活着 —— 报告说「什么都没贡献」,
        实际工具能调、路由能访问。这比直接失败更糟:没有任何信号。

        卸载时也走这里(见 :meth:`unload`)。effect 通常已经撤过,这里的删除是
        幂等的兜底 —— 与服务的处理同一个道理。

        **``agent.*`` 构造参数覆盖不在此列**:旧对象没有被保留,回滚不了。
        设计文档 §8.4 写明了这个边界。
        """
        if agent is not None:
            tools = set(contributed.get("tools", ()))
            if tools:
                for group in agent.toolkit.tool_groups:
                    group.tools = [t for t in group.tools if t.name not in tools]
            for name in contributed.get("middlewares", ()):
                _remove_one_middleware(agent, name)

        self.ctx.remove_owned(plugin_id)
        if self.routes is not None:
            self.routes.remove_owned(plugin_id)
        if self.commands is not None:
            self.commands.remove_owned(plugin_id)

    # ------------------------------------------------------------------ 卸载

    async def unload(self, plugin_id: str) -> bool:
        """卸载一个插件:调它的 effect,然后按归属撤掉剩下的痕迹。

        只调 effect 是不够的 —— 插件没写 effect(或写得不全)时,工具与中间件
        会留在 Agent 上,而记录已经是 ``UNLOADED``。报告自相矛盾,调用方也拿不到
        「真的卸载了」这个保证。
        """
        record = self.records.get(plugin_id)
        if record is None or record.state is not PluginState.LOADED:
            return False

        await self._run_effect(plugin_id, record.effect)
        self._effects = [(pid, e) for pid, e in self._effects if pid != plugin_id]

        self._rollback(self._agent, plugin_id, record.contributed)
        # 工具与中间件已经撤了,归属信息随之作废;``agent_overrides`` 保留 ——
        # 那个覆盖还在生效(回滚不了,见 _rollback 的说明)。
        record.contributed["tools"] = []
        record.contributed["middlewares"] = []

        record.state = PluginState.UNLOADED
        record.ctx = None
        record.effect = None
        logger.info("%s 已卸载", plugin_id)
        return True

    async def _run_effect(self, plugin_id: str, effect: Effect | None) -> None:
        if effect is None:
            return
        await run_effect(effect, label=f"{plugin_id} 的 effect")

    async def shutdown(self) -> None:
        """逆序调用全部 effect。"""
        for plugin_id, effect in reversed(self._effects):
            await run_effect(effect, label=f"{plugin_id} 的 effect")
        self._effects.clear()
        for record in self.records.values():
            if record.state is PluginState.LOADED:
                record.state = PluginState.UNLOADED
                record.ctx = None
        self._modules.clear()

    # ------------------------------------------------------------------ 重载

    def clear_module_cache(self) -> list[str]:
        """清掉所有插件模块及其私有子模块的缓存。返回被清掉的键。

        **必须按前缀清 ``sys.modules``** —— 只清顶层插件模块不够:目录形态
        插件的私有辅助模块注册为 ``sidecar_plugin_<id>.<sub>``,不清的话改了
        ``helper.py`` 不会生效。
        """
        keys = [k for k in sys.modules if k.startswith(MODULE_PREFIX)]
        for key in keys:
            sys.modules.pop(key, None)
        self._modules.clear()
        return keys

    async def reload(
        self,
        agent: Any,
        *,
        base_dirs: Sequence[Path] = (),
        extensions: Sequence[Path] = (),
    ) -> list[PluginRecord]:
        """卸载全部插件 → 清模块缓存 → 重新扫描、导入、激活。

        调用方负责在重载前重建 Agent —— 它持有旧插件贡献的中间件实例。
        """
        await self.shutdown()
        self.records.clear()
        self.shadowed_records.clear()
        self.clear_module_cache()
        logger.info("插件已重载")
        return await self.load_all(
            agent, base_dirs=base_dirs, extensions=extensions,
        )

    # ------------------------------------------------------------------ 报告

    def summary(self) -> list[dict[str, Any]]:
        order = {p: i for i, p in enumerate(self.options.plugin_dirs)}
        out = []
        for record in self.all_records():
            item = record.to_dict()
            item["order"] = order.get(record.root, -1)
            out.append(item)
        return sorted(out, key=lambda r: (r["order"], r["id"]))


def _is_async(func: Any) -> bool:
    import inspect

    return inspect.iscoroutinefunction(func)


def _remove_one_middleware(agent: Any, class_name: str) -> bool:
    """按类名摘掉**一个**中间件实例。找到并摘掉返回 True。

    只摘一个:同一插件可以挂同一个类的多个实例,``contributed`` 里就有多条记录,
    逐条摘即可。摘错了对象比摘不掉更糟,所以只动第一个匹配。
    """
    for bucket in MIDDLEWARE_BUCKETS:
        current = getattr(agent, bucket, None)
        if not current:
            continue
        for index, mw in enumerate(current):
            if type(mw).__name__ == class_name:
                del current[index]
                return True
    return False


def _apply_extensions(
    result: ScanResult, extensions: Sequence[Path], *, start_order: int,
) -> None:
    """把 ``-e`` 指定的单个插件文件按**最高优先级**并入扫描结果。

    它们不是目录,``scan`` 看不见,所以在这里手工插入;若与已发现的插件同 id,
    原有那个降级为 SHADOWED。

    **插件名要在这里再校验一次。** ``-e`` 绕过了 ``_classify``,不校验的话
    ``-e my-plugin.py`` 会造出 id ``my-plugin`` 而模块键是
    ``sidecar_plugin_my_plugin`` —— 正好是扫描路径严防的那个折叠冲突。
    """
    for index, path in enumerate(extensions):
        plugin_id = path.stem
        if not valid_plugin_name(plugin_id):
            result.warnings.append(invalid_name_warning(path, plugin_id))
            continue

        source = PluginSource(
            path=path, root=path.parent, is_package=False,
            plugin_id=plugin_id, order=start_order + index,
        )
        existing = next(
            (s for s in result.sources if s.plugin_id == plugin_id), None,
        )
        if existing is not None:
            result.shadowed.append((existing, source))
            result.sources = [s for s in result.sources
                              if s.plugin_id != plugin_id]

        result.sources.append(source)


__all__ = [
    "BASE_OWNER",
    "MIDDLEWARE_BUCKETS",
    "OVERRIDE_FIELDS",
    "PluginLoader",
    "PluginRecord",
    "PluginState",
    "snapshot",
]
