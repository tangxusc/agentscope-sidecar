"""故障隔离矩阵(设计文档 §4.2 / §8.4)。

**一个插件坏掉不该拖垮其余的。** 每个失败路径都要同时断言**状态**和
**错误消息** —— 只断言 ``state == failed`` 是不够的,``NameError`` 也会让插件
FAILED,断言不出真正的原因。
"""

from __future__ import annotations

import pytest

from expected import FAULTY_PLUGINS

BUILTIN = ("skills", "system_prompt")


def _states(sidecar) -> dict[str, str]:
    return {
        item["id"]: item["state"]
        for item in sidecar.report()["plugins"]
        if item["id"] not in BUILTIN
    }


def _errors(sidecar) -> dict[str, str]:
    return {
        item["id"]: item["error"] or ""
        for item in sidecar.report()["plugins"]
        if item["id"] not in BUILTIN
    }


def test_fault_matrix(faulty) -> None:
    sidecar = faulty.boot()
    try:
        states = _states(sidecar)
        errors = _errors(sidecar)
        assert set(states) == set(FAULTY_PLUGINS)

        for plugin_id, keyword in FAULTY_PLUGINS.items():
            if keyword is None:
                assert states[plugin_id] == "loaded", (
                    f"{plugin_id} 应当加载成功:{errors[plugin_id]}"
                )
            else:
                assert states[plugin_id] == "failed", (
                    f"{plugin_id} 应当失败,实际 {states[plugin_id]}"
                )
                assert keyword in errors[plugin_id], (
                    f"{plugin_id} 的报错不含 {keyword!r}:{errors[plugin_id]!r}"
                )
    finally:
        sidecar.shutdown()


def test_one_failure_does_not_block_the_rest(faulty) -> None:
    """坏的坏、好的好 —— 同一个目录里的插件互不影响。"""
    sidecar = faulty.boot()
    try:
        states = _states(sidecar)
        assert states["dup_route_a"] == "loaded"
        assert states["dup_route_b"] == "failed"
        assert states["dup_service_a"] == "loaded"
        assert states["dup_service_b"] == "failed"
    finally:
        sidecar.shutdown()


def test_import_error_keeps_original_exception_type(faulty) -> None:
    """导入失败要带上原始异常类型 —— 否则「声明写错」和「语法错」长得一样。"""
    sidecar = faulty.boot()
    try:
        error = _errors(sidecar)["broken_import"]
        assert "PluginImportError" in error
        assert "ModuleNotFoundError" in error
        assert "no_such_module_xyz" in error
    finally:
        sidecar.shutdown()


def test_sync_setup_is_rejected_with_reason(faulty) -> None:
    sidecar = faulty.boot()
    try:
        error = _errors(sidecar)["broken_signature"]
        assert "async def" in error
        assert "Toolkit" in error
    finally:
        sidecar.shutdown()


def test_variadic_signature_is_rejected(faulty) -> None:
    sidecar = faulty.boot()
    try:
        assert "*args" in _errors(sidecar)["variadic"]
    finally:
        sidecar.shutdown()


def test_route_conflict_names_both_owners(faulty) -> None:
    sidecar = faulty.boot()
    try:
        error = _errors(sidecar)["dup_route_b"]
        assert "dup_route_a" in error
        assert "dup_route_b" in error
    finally:
        sidecar.shutdown()


def test_service_conflict_names_both_owners(faulty) -> None:
    sidecar = faulty.boot()
    try:
        error = _errors(sidecar)["dup_service_b"]
        assert "dup_service_a" in error
        assert "dup_service_b" in error
    finally:
        sidecar.shutdown()


def test_failed_plugin_leaves_no_trace_in_registries(faulty) -> None:
    """失败插件的半成品注册要被清掉 —— 否则报告会撒谎。"""
    sidecar = faulty.boot()
    try:
        # broken_setup 抛在 provide 之前,但 broken_import 连模块都没进来
        assert sidecar.ctx.get("shared_name") == 1     # 只有先注册的那个
        assert "shared_name" not in sidecar.loader.records["dup_service_b"].contributed
    finally:
        sidecar.shutdown()


def test_broken_effect_does_not_block_shutdown(faulty) -> None:
    """effect 抛错不阻断其余 effect,也不让 shutdown 抛出去。"""
    sidecar = faulty.boot()
    sidecar.shutdown()                                 # 不该抛
    assert sidecar.loader.records["broken_effect"].state.value == "unloaded"


def test_failed_plugin_is_not_unloadable(faulty) -> None:
    import asyncio

    sidecar = faulty.boot()
    try:
        assert asyncio.run(sidecar.unload("broken_setup")) is False
    finally:
        sidecar.shutdown()


def test_plugin_failed_event_is_emitted(faulty) -> None:
    """失败要广播出去 —— 外部观察者不该只靠轮询报告。"""
    events: list[tuple[str, dict]] = []

    faulty.plugin("observer", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context, events) -> None:
            def record(**payload: object) -> None:
                ctx.provide("seen", dict(payload))

            events.on("sidecar/plugin-failed", record)
    ''')
    sidecar = faulty.boot()
    try:
        seen = sidecar.ctx.get("seen")
        assert seen is not None
        assert seen["plugin"] in FAULTY_PLUGINS
        assert seen["error"]
    finally:
        sidecar.shutdown()
    assert events == []


@pytest.mark.parametrize("plugin_id", sorted(FAULTY_PLUGINS))
def test_each_faulty_plugin_is_isolated(faulty, plugin_id: str) -> None:
    """逐个确认:拿掉任何一个,其余照常。"""
    sidecar = faulty.boot()
    try:
        states = _states(sidecar)
        healthy = {
            k for k, v in FAULTY_PLUGINS.items()
            if v is None and k != plugin_id
        }
        for name in healthy:
            assert states[name] == "loaded", (
                f"因为 {plugin_id} 坏了,{name} 也没加载上"
            )
    finally:
        sidecar.shutdown()


def test_halfway_failure_leaves_nothing_behind(faulty) -> None:
    """``setup`` 半途抛异常 —— 已经注册的东西必须全部回滚。

    不回滚的话,插件的状态是 ``failed`` 而工具能调、路由能访问、服务还在容器里。
    报告说「什么都没贡献」,实际是活的 —— 比直接失败更糟,因为没有任何信号。
    """
    sidecar = faulty.boot()
    try:
        report = sidecar.report()
        assert sidecar.loader.records["halfway"].state.value == "failed"
        assert "装到一半炸了" in sidecar.loader.records["halfway"].error

        assert "ghost_tool" not in report["tools"]
        assert "ghost_tool" not in report["tool_owners"]
        assert "GET /ghost" not in report["routes"]
        assert "ghost" not in report["commands"]
        assert "ghost_service" not in report["services"]
        assert sidecar.ctx.get("ghost_service") is None
        assert sidecar.commands.get("ghost") is None
    finally:
        sidecar.shutdown()


def test_halfway_failure_keeps_other_plugins_working(faulty) -> None:
    """回滚只动失败插件自己的东西。"""
    sidecar = faulty.boot()
    try:
        report = sidecar.report()
        assert report["routes"] == {"GET /dup": "dup_route_a"}
        assert report["services"]["shared_name"] == "dup_service_a"
    finally:
        sidecar.shutdown()
