"""多插件目录:优先级、覆盖、跨目录服务、``-e``(设计文档 §4.1)。"""

from __future__ import annotations

import pytest

from agentscope_sidecar import PluginState


def _ids(sidecar) -> set[str]:
    return {item["id"] for item in sidecar.report()["plugins"]}


def test_later_directory_wins(shared, base_dir, biz_dir) -> None:
    """``common`` 两个目录里都有 —— 生效的是后面那个。"""
    sidecar = shared.boot(plugins=(str(base_dir), str(biz_dir)))
    try:
        assert sidecar.loader.records["common"].root == biz_dir
        names = [type(m).__name__ for m in sidecar.build_agent()._model_call_middlewares]
        assert names == ["BizMarker"]
    finally:
        sidecar.shutdown()


def test_shadowed_record_is_kept(shared, base_dir, biz_dir) -> None:
    sidecar = shared.boot(plugins=(str(base_dir), str(biz_dir)))
    try:
        shadowed = [
            item for item in sidecar.report()["plugins"]
            if item["state"] == "shadowed"
        ]
        assert len(shadowed) == 1
        assert shadowed[0]["id"] == "common"
        assert shadowed[0]["shadowed_by"] == str(biz_dir / "common.py")
    finally:
        sidecar.shutdown()


def test_plugin_from_lower_priority_still_loads(shared, base_dir, biz_dir) -> None:
    sidecar = shared.boot(plugins=(str(base_dir), str(biz_dir)))
    try:
        assert sidecar.loader.records["greeting"].root == base_dir
        assert sidecar.loader.records["consumer"].root == base_dir
        assert sidecar.loader.records["only_here"].root == biz_dir
    finally:
        sidecar.shutdown()


def test_cross_directory_service_consumption(shared, base_dir, biz_dir) -> None:
    """``consumer`` 与 ``greeting`` 分属不同目录,且 ``consumer`` 先加载。

    注入拿到惰性代理,等 ``sidecar/ready`` 再读 —— 加载顺序无关。
    """
    sidecar = shared.boot(plugins=(str(base_dir), str(biz_dir)))
    try:
        log = (shared.root / "consumer.log").read_text(encoding="utf-8")
        assert log == "consumer|来自 base 的问候"
    finally:
        sidecar.shutdown()


def test_swapping_order_swaps_winner(shared, base_dir, biz_dir) -> None:
    sidecar = shared.boot(plugins=(str(biz_dir), str(base_dir)))
    try:
        assert sidecar.loader.records["common"].root == base_dir
        names = [type(m).__name__ for m in sidecar.build_agent()._model_call_middlewares]
        assert names == ["BaseMarker"]
    finally:
        sidecar.shutdown()


def test_default_plugins_dir_is_always_last(shared, base_dir) -> None:
    """``./plugins`` 恒在最后,即使 ``--plugins`` 里也给了目录。"""
    sidecar = shared.boot(plugins=(str(base_dir),))
    try:
        dirs = sidecar.report()["plugin_dirs"]
        assert dirs[-1] == str(shared.root / "plugins")
        assert dirs[0] == str(base_dir)
    finally:
        sidecar.shutdown()


def test_builtin_dir_is_lowest_priority(shared, base_dir) -> None:
    """内置插件排在最前 —— 用户插件能覆盖它。"""
    sidecar = shared.boot(plugins=(str(base_dir),))
    try:
        assert _ids(sidecar) >= {"skills", "system_prompt"}
        order = [item["id"] for item in sidecar.report()["plugins"]]
        assert order[0] == "skills"
    finally:
        sidecar.shutdown()


def test_extension_file_has_highest_priority(shared, base_dir) -> None:
    """``-e`` 的临时文件覆盖同名插件。"""
    override = shared.root / "common.py"
    override.write_text(
        "from __future__ import annotations\n"
        "\n"
        "from agentscope.agent import Agent\n"
        "from agentscope.middleware import MiddlewareBase\n"
        "\n"
        "\n"
        "class ExtMarker(MiddlewareBase):\n"
        "    async def on_model_call(self, agent: Agent, input_kwargs, next_handler):\n"
        "        return await next_handler(**input_kwargs)\n"
        "\n"
        "\n"
        "async def setup(agent: Agent) -> None:\n"
        "    agent._model_call_middlewares.append(ExtMarker())\n",
        encoding="utf-8",
    )
    sidecar = shared.boot(
        plugins=(str(base_dir),), extensions=(str(override),),
    )
    try:
        names = [type(m).__name__ for m in sidecar.build_agent()._model_call_middlewares]
        assert names == ["ExtMarker"]
        assert sidecar.report()["extensions"] == [str(override)]
    finally:
        sidecar.shutdown()


def test_extension_file_needs_no_directory(shared) -> None:
    """``-e`` 指向的是单个文件,不需要放进插件目录。"""
    path = shared.write(
        "one_off.py",
        '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent / "one_off.log"


        async def setup(ctx: Context) -> None:
            LOG.write_text(ctx.id, encoding="utf-8")
        ''',
    )
    sidecar = shared.boot(extensions=(str(path),))
    try:
        assert (shared.root / "one_off.log").read_text(encoding="utf-8") == "one_off"
    finally:
        sidecar.shutdown()


def test_env_var_supplies_plugin_dirs(shared, base_dir, monkeypatch) -> None:
    monkeypatch.setenv("SIDECAR_PLUGINS", str(base_dir))
    sidecar = shared.boot()
    try:
        assert sidecar.loader.records["greeting"].root == base_dir
    finally:
        sidecar.shutdown()


def test_cli_plugins_overrides_env_var(shared, base_dir, biz_dir, monkeypatch) -> None:
    """给了 ``--plugins`` 就**忽略** ``SIDECAR_PLUGINS`` —— 不叠加。"""
    monkeypatch.setenv("SIDECAR_PLUGINS", str(biz_dir))
    sidecar = shared.boot(plugins=(str(base_dir),))
    try:
        assert "greeting" in sidecar.loader.records        # 来自 --plugins
        assert "only_here" not in sidecar.loader.records   # 环境变量被忽略
    finally:
        sidecar.shutdown()


@pytest.mark.parametrize("name", ["my-plugin", "123abc", "class"])
def test_invalid_plugin_name_is_skipped(bare, name: str) -> None:
    """非法标识符会撞模块键 —— 跳过并告警,不静默覆盖。"""
    bare.write(f"plugins/{name}.py", "x = 1\n")
    sidecar = bare.boot()
    try:
        assert name not in sidecar.loader.records
        assert any("不是合法的 Python 标识符" in w
                   for w in sidecar.report()["warnings"])
    finally:
        sidecar.shutdown()


def test_module_key_collision_is_the_reason(bare) -> None:
    """根因:``\\W`` 归一化让三个不同名字映射到同一个模块键。"""
    from agentscope_sidecar.discover import module_key

    keys = {module_key(n) for n in ("my-plugin", "my_plugin", "my.plugin")}
    assert len(keys) == 1


def test_plugin_state_enum_is_complete() -> None:
    assert {s.value for s in PluginState} == {
        "loaded", "failed", "skipped", "shadowed", "unloaded",
    }
