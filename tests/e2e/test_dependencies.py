"""依赖注入的端到端行为(设计文档 §5)。

夹具在 ``agent/optional/``:一个依赖永不存在的服务、一个可选依赖、
一个后加载的提供方。
"""

from __future__ import annotations


def test_missing_dependency_becomes_lazy_proxy(optional) -> None:
    """没有默认值的参数拿惰性代理,不报错 —— 首次使用才解析。"""
    sidecar = optional.boot()
    try:
        record = sidecar.loader.records["needs_missing"]
        assert record.state.value == "loaded"
        assert record.unresolved == ["never_provided"]
        assert sidecar.report()["unresolved"] == ["never_provided"]
    finally:
        sidecar.shutdown()


def test_optional_dependency_absent_at_setup(optional) -> None:
    """``soft_dep`` 比 ``z_provider`` 先加载 —— ``ctx.get`` 拿到 None。"""
    sidecar = optional.boot()
    try:
        log = (optional.root / "soft.log").read_text(encoding="utf-8")
        assert log == "没有 store"
        assert sidecar.ctx.get("store") == {"k": 1}    # 全部加载完之后有了
    finally:
        sidecar.shutdown()


def test_soft_dependency_does_not_appear_in_unresolved(optional) -> None:
    """有默认值的参数不注入,也不算未解析。"""
    sidecar = optional.boot()
    try:
        assert sidecar.loader.records["soft_dep"].unresolved == []
    finally:
        sidecar.shutdown()


def test_resolved_dependency_is_not_in_unresolved(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert sidecar.loader.records["consumer"].unresolved == []
    finally:
        sidecar.shutdown()


def test_provided_service_is_used_by_later_plugin(bare) -> None:
    """``ctx.provide`` 之后,后加载的插件按名称拿到真身。"""
    bare.plugin("a_provider", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context) -> None:
            ctx.provide("thing", {"n": 1})
    ''')
    bare.plugin("b_user", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "b.txt"


        async def setup(thing, ctx: Context) -> None:
            LOG.write_text(f"{thing['n']}|{ctx.id}", encoding="utf-8")
    ''')
    sidecar = bare.boot()
    try:
        assert (bare.root / "b.txt").read_text(encoding="utf-8") == "1|b_user"
    finally:
        sidecar.shutdown()


def test_service_conflict_between_plugins(bare) -> None:
    bare.plugin("a", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context) -> None:
            ctx.provide("x", 1)
    ''')
    bare.plugin("b", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context) -> None:
            ctx.provide("x", 2)
    ''')
    sidecar = bare.boot()
    try:
        assert sidecar.loader.records["a"].state.value == "loaded"
        assert sidecar.loader.records["b"].state.value == "failed"
        assert "不能重复注册" in sidecar.loader.records["b"].error
        assert sidecar.ctx.get("x") == 1
    finally:
        sidecar.shutdown()


def test_same_plugin_can_reprovide(bare) -> None:
    """同一归属重复 provide 是覆盖,不是冲突。"""
    bare.plugin("a", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context) -> None:
            ctx.provide("x", 1)
            ctx.provide("x", 2)
    ''')
    sidecar = bare.boot()
    try:
        assert sidecar.ctx.get("x") == 2
        assert sidecar.ctx.by_type(int) == ["x"]
    finally:
        sidecar.shutdown()


def test_type_index_is_cleaned_on_remove(bare) -> None:
    bare.plugin("a", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context) -> None:
            ctx.provide("x", 1)
            ctx.remove("x")
    ''')
    sidecar = bare.boot()
    try:
        assert sidecar.ctx.by_type(int) == []
        assert sidecar.ctx.get("x") is None
    finally:
        sidecar.shutdown()


def test_underscore_parameter_is_never_injected(bare) -> None:
    bare.plugin("a", '''
        from pathlib import Path

        from agentscope_sidecar import Context


        async def setup(_agent=None, ctx: Context = None) -> None:
            Path(__file__).parent.parent.joinpath("u.txt").write_text(
                f"{_agent}|{ctx is None}", encoding="utf-8",
            )
    ''')
    sidecar = bare.boot()
    try:
        assert (bare.root / "u.txt").read_text(encoding="utf-8") == "None|False"
    finally:
        sidecar.shutdown()


def test_underscore_without_default_is_an_error(bare) -> None:
    bare.plugin("a", '''
        async def setup(_agent) -> None:
            pass
    ''')
    sidecar = bare.boot()
    try:
        record = sidecar.loader.records["a"]
        assert record.state.value == "failed"
        assert "必须给默认值" in record.error
    finally:
        sidecar.shutdown()


def test_unresolvable_annotation_falls_back_to_lazy(bare) -> None:
    """注解引用了插件里看不见的名字 —— 不报错,退回惰性代理。

    插件普遍写 ``from __future__ import annotations``,注解是字符串;
    ``get_type_hints`` 解析不了时必须吞掉异常。不能因为一个注解拼错就让插件加载
    失败。
    """
    bare.plugin("a_user", '''
        from agentscope_sidecar import Context


        async def setup(store: "NotImportedAnywhere", ctx: Context) -> None:
            pass
    ''')
    sidecar = bare.boot()
    try:
        record = sidecar.loader.records["a_user"]
        assert record.state.value == "loaded"
        assert record.unresolved == ["store"]
    finally:
        sidecar.shutdown()


def test_injection_by_type(bare) -> None:
    """按类型注入:容器里该类型恰好一个服务时生效。"""
    bare.plugin("a_provider", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context) -> None:
            ctx.provide("numbers", {"n": 42})
    ''')
    bare.plugin("b_user", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "t.txt"


        async def setup(bag: dict, ctx: Context) -> None:
            LOG.write_text(str(bag["n"]), encoding="utf-8")
    ''')
    sidecar = bare.boot()
    try:
        assert (bare.root / "t.txt").read_text(encoding="utf-8") == "42"
        assert sidecar.loader.records["b_user"].unresolved == []
    finally:
        sidecar.shutdown()


def test_name_wins_over_type(bare) -> None:
    """名称精确、类型宽松 —— 两者都能命中时按名称。"""
    bare.plugin("a_provider", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context) -> None:
            ctx.provide("numbers", {"n": 1})
            ctx.provide("other", {"n": 2})
    ''')
    bare.plugin("b_user", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "n.txt"


        async def setup(numbers: dict, ctx: Context) -> None:
            LOG.write_text(str(numbers["n"]), encoding="utf-8")
    ''')
    sidecar = bare.boot()
    try:
        # 按类型会有两个 dict 候选,按名称只有一个 —— 名称优先
        assert (bare.root / "n.txt").read_text(encoding="utf-8") == "1"
        assert sidecar.loader.records["b_user"].state.value == "loaded"
    finally:
        sidecar.shutdown()


def test_ambiguous_type_is_an_error(bare) -> None:
    """名字对不上、类型又匹配到多个 —— 报错,不猜。"""
    bare.plugin("a_provider", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context) -> None:
            ctx.provide("one", {"a": 1})
            ctx.provide("two", {"b": 2})
    ''')
    bare.plugin("b_user", '''
        from agentscope_sidecar import Context


        async def setup(thing: dict, ctx: Context) -> None:
            pass
    ''')
    sidecar = bare.boot()
    try:
        record = sidecar.loader.records["b_user"]
        assert record.state.value == "failed"
        assert "匹配到多个服务" in record.error
    finally:
        sidecar.shutdown()
