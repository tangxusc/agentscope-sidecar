# agentscope-sidecar 设计

本文是实现规格:接口签名、数据结构、算法、异常分类、事件载荷、CLI 规格。
照着能编码,不做方案对比,不记决策过程。

未实现的部分标 `[未实现]`。

---

## 1. 架构

插件是**插件目录里的一个 `.py` 文件**(或一个带 `__init__.py` 的目录),唯一的入口是
模块级的 `async def setup(...)`。底座先把 Agent 建好,再按顺序调每个插件的 `setup`,
把 Agent 交给它们改。

```
解析选项 → 建容器 → 建 Agent → 扫描插件 → 逐个 await setup(签名注入)
        → 应用 --tools / --middlewares 过滤 → 跑运行模式
        → 退出时逆序调 effect
```

| 关注点 | 由谁负责 |
| --- | --- |
| 建 Agent(模型、空 toolkit、权限上下文) | 底座 |
| 挂工具、中间件、提示词、路由、服务 | 插件 |
| 归属追踪(谁注册了什么) | 底座(快照差分 + `current_owner()`) |
| 清理 | 插件返回的 effect,底座逆序调用 |

**没有声明收集,没有清单文件,没有 `REQUIRES`。** 插件直接对拿到的对象做事。

---

## 2. 核心模型

三个对象,其余都是它们的一部分。

### 2.1 `agent`

AgentScope 的 `Agent` 实例。**构造后完全可变**,所有字段调用时才读:

| 字段                       | 类型                  | 插件怎么用                                                     |
| ------------------------ | ------------------- | --------------------------------------------------------- |
| `agent.toolkit`          | `Toolkit`           | `await agent.toolkit.add_tool(...)` / `remove_tool(name)` |
| `agent._system_prompt`   | `str`               | 直接赋值;片段拼接用中间件                                             |
| `agent._*_middlewares`   | `list`              | 直接 `append` / `remove`                                    |
| `agent.model`            | `ChatModelBase`     | 直接赋值替换                                                    |
| `agent.model_config`     | `ModelConfig`       | 直接赋值                                                      |
| `agent.context_config`   | `ContextConfig`     | 直接赋值                                                      |
| `agent.injection_config` | `InjectionConfig`   | 直接赋值                                                      |
| `agent.react_config`     | `ReActConfig`       | 直接赋值                                                      |
| `agent.offloader`        | `Offloader \| None` | 直接赋值                                                      |
| `agent.state`            | `AgentState`        | 读;权限上下文在 `state.permission_context`                       |

**`agent.middlewares` 不存在。** 构造参数 `middlewares` 只在 `Agent.__init__` 里被读一次,  
用来分桶,之后不再保存。**构造后改它没有任何效果** —— 只能改桶(§5.4)。

**中间件的七个桶**(构造时按 `is_implemented` 分好,普通 list):

```
_acting_middlewares            _model_call_middlewares
_reasoning_middlewares         _reply_middlewares
_system_prompt_middlewares     _check_permission_middlewares
_compress_context_middlewares
```

插件往对应桶里 append。分桶规则见 §5.4。

**`_system_prompt` 与 `_system_prompt_middlewares` 的关系**:见 §5.5。多个插件贡献提示词片段时,  
**用中间件,不要字符串拼接** —— 拼接的顺序取决于 setup 调用顺序,不可控。

### 2.2 `ctx`

服务容器,同时是注入给插件的那个对象。**每个插件拿到的是自己的视图** —— 共享
同一份服务存储,但 `ctx.id` / `ctx.dir` / `ctx.logger` 是这个插件的。

**完整接口在 §6.1**,这里只说三件事:

1. `ctx.provide(name, value)` 立即生效 —— 之后加载的插件按名字就能注入到它。
2. `ctx.require(name)` 在服务还不存在时返回**惰性代理**,首次使用才解析。
3. `ctx.on(event, handler)` 是 `events.on` 的转发,**返回退订函数**。

`ctx` 参数名是保留的:签名叫 `ctx` 的参数总是注入插件自己的视图,不会去容器里
找一个叫 `ctx` 的服务。

### 2.3 `options`

`RuntimeOptions`(§9)。只读。

---

## 3. 生命周期

```
1. 解析运行选项                      → RuntimeOptions
2. 建容器,注册内置服务               → agent 之外的一切(§6)
3. 扫描插件目录                      → 按 plugin_dirs 优先级 + 名字排序
4. 导入插件模块                      → 只 import,不执行 setup
5. 建默认 model 实例                 → 内置 provider 表 + CLI/环境变量(§9.3)
6. 建 Agent(空 toolkit / 空中间件 / 空提示词)
7. 把 agent 注册进容器
8. 按顺序调用每个 setup               → 按签名注入(§5)
9. 收集 effect                       → setup 返回值
10. 应用 --tools / --middlewares 过滤
11. 发 sidecar/ready
12. 跑运行模式
13. 退出:先发 sidecar/shutdown,再逆序调用 effect
```

第 8 步里单个插件抛异常**不影响其余**:记 `FAILED`、回滚它已经做的注册(§8.4)、
继续下一个。

**第 13 步的顺序不能反。** 插件要在**还接着线**的时候收到关闭通知 —— 反过来的话,
effect 已经把订阅撤了(或把日志文件删了),紧接着那条广播又会被残留订阅者写一遍。

**第 6 步的 Agent 是"空壳"** —— 没有任何工具、中间件、提示词。所有能力由第 8 步的插件挂上。

**第 8 步的顺序** = 扫描顺序(plugin_dirs 后者优先,同目录按文件名)。依赖不靠顺序保证 ——  
靠签名注入拿到真实对象或惰性代理(§5.2)。

---

## 4. 插件规范

### 4.1 形态

插件目录的**顶层条目**就是插件,两种形态:

| 顶层条目 | 形态 | 导入的是 |
| --- | --- | --- |
| `x.py` | 单文件 | `x.py` |
| `x/__init__.py` | 目录 | `__init__.py`(内部其余文件是私有配套) |

**扫描不递归。** 只有顶层算数 —— 目录形态的配套模块、数据、模板都放在插件目录
内部,不参与扫描。

| 顶层条目 | 处理 |
| --- | --- |
| `*.py` | 插件 |
| 含 `__init__.py` 的目录 | 插件 |
| `_` 开头 | **移出插件集**(私有、临时、停用) |
| `__pycache__` / `*.pyc` | 忽略 |
| 其他文件 / 不含 `__init__.py` 但含 `.py` 的目录 | 忽略;后者**告警** |

**插件 id 恒为单段**,取顶层名字。目录形态取目录名。

**id 必须是合法 Python 标识符且不是关键字**,否则跳过并告警。这不是风格要求:
`module_key()` 用 `\W -> _` 归一化,`my-plugin` / `my_plugin` / `my.plugin` 会
折叠成同一个 `sys.modules` 键 —— 两个插件共用一份模块对象,后者覆盖前者,而两边
都报 `loaded`。id 还会进入 `ctx.logger` 的名字与服务名,含 `-` 在那里站不住。

**`_` 前缀只有「移出插件集」一个语义。** 停用插件就是加 `_` 前缀。

### 4.2 唯一入口:`setup`

```python
# plugins/weather.py
from agentscope.agent import Agent
from agentscope.tool import FunctionTool, ToolResponse
from agentscope.message import TextBlock

from agentscope_sidecar import Context, Effect


async def get_weather(city: str) -> ToolResponse:
    """查询指定城市的实时天气。

    Args:
        city (str): 城市名
    """
    return ToolResponse(content=[TextBlock(type="text", text=f"{city} 晴")])


async def setup(agent: Agent, ctx: Context, events) -> Effect:
    await agent.toolkit.add_tool(FunctionTool(get_weather))

    def on_ready(**_: object) -> None:
        ctx.logger.info("天气插件就绪")

    dispose = events.on("sidecar/ready", on_ready)      # 返回退订函数,不是装饰器

    def cleanup() -> None:
        dispose()
        # remove_tool 是异步的 —— effect 允许 async,但这里用同步版本更简单:
        # Agent 已经要被丢掉时不必逐个摘

    return cleanup
```

**规则:**

1. `setup` 是**唯一入口**。没有 `setup` 的插件 → `SKIPPED`(不报错,`inspect` 里列出)。
2. **没有装饰器。** 一切通过参数拿到的对象完成。`events.on(...)` 返回退订函数,
   直接调用,不能当装饰器用。
3. `setup` **必须是 `async def`** —— `Toolkit.add_tool` / `remove_tool` 是异步的。
   同步的 `setup` 直接判 `FAILED`,报错说明原因。
4. `setup` 的返回值是 effect,或 `None`(没有需要清理的东西)。
5. 参数按名字注入(§5)。签名不能有 `*args` / `**kwargs`。
6. `setup` 半途抛异常时,底座会**回滚**它已经做的注册(§8.4)。

### 4.3 `Effect`

```python
Effect = Callable[[], None | Awaitable[None]]
```

- **无参数**。同步或 `async` 都可以 —— `Toolkit.remove_tool` 是异步的,禁了异步
  就没法在 effect 里摘工具。
- 底座在退出时**逆序**调用所有 effect。
- 单个 effect 抛异常**不阻断**其余 —— 记日志,继续往下调。
- 卸载单个插件时,只调该插件的 effect。

**effect 不是唯一的清理途径。** 卸载插件时底座还会按归属**兜底回滚**(§8.4):
摘掉它贡献的工具、中间件、路由、命令、服务。effect 写漏了不会留下垃圾。

---

## 5. 依赖注入

### 5.1 形式

**构造函数注入** —— `setup` 的参数列表就是依赖声明。按**参数名**解析:

```python
def setup(agent, options, events, routes, weather) -> Effect:
    #   ↑ 全部按名字从容器解析
```

### 5.2 解析规则

按参数逐个判定,顺序如下:

| 条件             | 注入                        |
| -------------- | ------------------------- |
| 名字以 `_` 开头     | **不注入**,用默认值(若没有默认值 → 报错) |
| 容器里有这个名字       | 真实对象                      |
| 没有,但参数**有默认值** | 用默认值,不注入                  |
| 没有,且**无默认值**   | **惰性代理**(`ServiceRef`)    |

惰性代理在首次使用属性/调用时才解析;那时若仍不存在 → `ServiceNotFound`。  
**未解析的依赖会记进 `inspect` 报告的 `unresolved` 字段** —— 惰性不等于不可观测。

```python
class ServiceRef:
    """惰性服务引用。转发属性、调用、容器协议、字符串化。"""
    def __getattr__(self, item: str) -> Any: ...    # getattr(真身, item)
    def __setattr__(self, key: str, value: Any) -> None: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...
    def __getitem__ / __setitem__ / __delitem__ / __contains__ / __iter__ / __len__
    def __str__ / __format__ / __bool__               # f"{ref}" / f"{ref:>10}" / if ref
    def __repr__(self) -> str: ...                   # <ServiceRef x (ready|pending)>
```

**为什么连 `__str__` / `__format__` / `__bool__` 也要转发**:只转发 `__repr__`
的话,``f"{ref}"`` 走的是 ``object.__str__`` → 我们自己的 ``__repr__``,结果打出
``<ServiceRef greeting (ready)>`` 而不是服务本身的值。**看起来像没解析成功,其实
是解析了但打印错了** —— 这类错误最难查,因为它不报错。

**不转发 `__eq__` / `__hash__`**:定义 `__eq__` 会让对象变成不可哈希,而插件常把
引用当字典键。要比较就用 `ctx.get(name) is value`。

### 5.3 什么时候用注入,什么时候用 `ctx.require`

| 场景                                                                         | 用                                 |
| -------------------------------------------------------------------------- | --------------------------------- |
| 内置服务(`agent` / `options` / `events` / `routes` / `commands` / `telemetry`) | **签名注入**                          |
| 插件提供的服务,加载顺序有保证                                                            | 签名注入                              |
| 插件提供的服务,顺序不保证 / 可能不存在                                                      | `ctx.require("x")` 显式惰性           |
| 可选依赖                                                                       | 参数给默认值 `None`,用 `ctx.get("x")` 判定 |

**不提供 `inject = [...]` 这类声明** —— 签名已经表达了依赖,再加一份就是「为了描述自己而写的第二个东西」。

### 5.4 中间件分桶

AgentScope 在 `Agent.__init__` 时按 `is_implemented(hook)` 把中间件分进七个桶。  
**插件直接往桶里 append**,不要走 `agent.middlewares`(那是构造参数,构造后不再被读)。

```python
# ✅
agent._model_call_middlewares.append(MyMiddleware())
# ❌ 构造后无效
agent.middlewares.append(...)
```

| 桶                               | hook                  | 用途              |
| ------------------------------- | --------------------- | --------------- |
| `_acting_middlewares`           | `on_acting`           | 包住单次工具执行        |
| `_model_call_middlewares`       | `on_model_call`       | 包住模型调用(重试、改写请求) |
| `_reasoning_middlewares`        | `on_reasoning`        | 包住整个推理循环        |
| `_reply_middlewares`            | `on_reply`            | 包住一轮回复          |
| `_system_prompt_middlewares`    | `on_system_prompt`    | **改写**系统提示词(见下) |
| `_check_permission_middlewares` | `on_check_permission` | 改写权限判定          |
| `_compress_context_middlewares` | `on_compress_context` | 改写上下文压缩         |

### 5.5 `on_system_prompt` 与其他 hook 不同

它是**纯变换**,没有 `next_handler`:

```python
class MiddlewareBase:
    async def on_system_prompt(self, agent: Agent, current_prompt: str) -> str: ...
```

其余六个是链式(`agent, input_kwargs, next_handler`)。`on_system_prompt` **必须是  
`async def`** —— 同步实现会被静默跳过。

**提示词的组装顺序**(`Agent._get_system_prompt`):

```
1. agent._system_prompt                    ← 底座/插件直接赋的
2. toolkit 的技能说明                        ← skills 插件挂的 loader
3. offloader 的说明(若 offloader 是 WorkspaceBase)
   ↓ "\n".join(...)
4. 依次经过 _system_prompt_middlewares      ← 插件片段在这里追加
   result = await mw.on_system_prompt(self, result)
```

所以插件贡献提示词片段的写法:

```python
class _Section(MiddlewareBase):
    async def on_system_prompt(self, agent, current_prompt: str) -> str:
        return f"{current_prompt}\n\n{self.text}"

agent._system_prompt_middlewares.append(_Section("片段文本"))
```

**追加顺序即拼接顺序**(`_system_prompt_middlewares` 是按 append 顺序遍历的)。  
多个插件都追加时,顺序 = setup 调用顺序 = 插件扫描顺序。

---

## 6. 服务目录

### 6.1 内置服务(容器启动时就有)

| 名字          | 类型                | 说明                         |
| ----------- | ----------------- | -------------------------- |
| `agent`     | `Agent`           | AgentScope Agent,插件的主要操作对象 |
| `options`   | `RuntimeOptions`  | 运行选项(只读)                   |
| `events`    | `EventBus`        | 事件总线                       |
| `routes`    | `RouteRegistry`   | HTTP 路由                    |
| `commands`  | `CommandRegistry` | CLI 子命令                    |
| `telemetry` | `Telemetry`       | 事件计数与落盘                    |

归属都是 `<base>`。**它们由底座在 boot 时注册**(§3 第 6 步),之后插件也能覆盖
同名服务 —— 但那样归属就变成插件了。

两个**不是**服务的东西:

- **`ctx`** 不是注册在容器里的服务。签名叫 `ctx` 的参数由注入器特殊处理,直接给
  插件自己的视图(§5.2 规则 0)。
- **`ctx.logger`** 是 `Context` 的属性,每个插件拿到的是绑定了自己 id 的 logger
  (`sidecar.plugin.<id>`)。

```python
class Context:
    def provide(self, name: str, value: Any) -> None: ...
    def remove(self, name: str) -> None: ...              # 不存在时静默返回
    def remove_owned(self, owner: str) -> list[str]: ...  # 按归属批量移除(§8.4)
    def get(self, name: str, default: Any = None) -> Any: ...
    def require(self, name: str) -> Any: ...              # 未就绪返回惰性代理
    def has(self, name: str) -> bool: ...
    def by_type(self, cls: type) -> list[str]: ...        # 精确匹配 type(value)
    def services(self) -> dict[str, str]: ...             # 名字 -> 归属
    def names(self) -> list[str]: ...                     # 已注册的名字,排序

    def on(self, event: str, handler: Callable) -> Callable[[], None]: ...
                                                          # 转发 events.on,返回退订
```

身份与元信息(只读属性):`id` / `dir` / `root` / `interactive` / `options` /
`events` / `logger`。`for_plugin(id, dir, root)` 派生一个新视图,共享服务存储。

**同名服务被不同归属注册时报错**,不静默覆盖 —— 否则两个插件抢一个名字,谁生效
取决于加载顺序,而报告里看不出异常。

### 6.2 `EventBus`

```python
class EventBus:
    def on(self, event: str, handler: Callable) -> Callable[[], None]:
        """订阅。返回取消订阅的函数。"""
    async def emit(self, event: str, **payload: Any) -> None: ...
```

处理器可以是同步或 async。同步处理器在事件循环里直接调用。

### 6.3 `RouteRegistry`

```python
class RouteRegistry:
    def add(self, method: str, path: str, handler: Callable,
            *, name: str | None = None) -> RouteSpec:
        """path 是完整路径,底座不加前缀。冲突报 DeclarationError。"""
    def remove(self, method: str, path: str) -> None: ...
    def remove_owned(self, owner: str) -> list[str]: ...   # 返回被移除的 "GET /x"
    def routes(self) -> list[RouteSpec]: ...
    def describe(self) -> dict[str, str]: ...              # "GET /x" -> 插件 id
```

处理器直接用 Starlette 的 `Request` / `Response`。

### 6.4 `CommandRegistry`

```python
class CommandRegistry:
    def add(self, name: str, handler: Callable[[str], None]) -> None: ...
    def remove(self, name: str) -> None: ...
    def remove_owned(self, owner: str) -> list[str]: ...
    def get(self, name: str) -> Callable | None: ...
    def names(self) -> list[str]: ...
    def describe(self) -> dict[str, str]: ...        # 名字 -> 插件 id
```

**两个注册表都记归属。** 路由与命令不在 Agent 上,没法靠快照差分归属 —— 注册时
读 `current_owner()`(加载器在 `setup` 期间设置)。没有这层,`setup` 半途失败就
回滚不掉已经注册的路由。

### 6.5 `Telemetry`

```python
class Telemetry:
    def record(self, name: str, payload: dict[str, Any]) -> None: ...
    counters: dict[str, int]
    def snapshot(self) -> dict[str, Any]: ...
```

`snapshot()` 返回 `{"elapsed": float, "counters": dict[str, int], "timeline_size": int}`。
`timeline` 是**有界**的(默认 1000 条):常驻服务跑几天不清理就是内存泄漏。
计数是无界的精确值,时间线只是最近的样本。

### 6.6 事件目录

事件名是纯字符串,没有任何静态检查能发现拼错或漏发。**这张表是唯一的事实来源**,
`tests/test_events_catalog.py` 会比对文档与代码。

#### 底座发出

| 事件 | 载荷 | 何时 |
| --- | --- | --- |
| `sidecar/boot` | `base_dir: str`,`plugin_dirs: list[str]` | 建完容器、建 Agent 之前 |
| `sidecar/plugin-loaded` | `plugin: str`,`root: str` | 每个插件激活成功 |
| `sidecar/plugin-shadowed` | `plugin: str`,`shadowed: str`,`winner: str` | 同名插件被高优先级目录覆盖 |
| `sidecar/plugin-failed` | `plugin: str`,`root: str`,`error: str` | 导入或 `setup` 抛异常 |
| `sidecar/plugin-unloaded` | `plugin: str` | `Sidecar.unload()` 成功 |
| `sidecar/ready` | `plugins: int` | 全部 `setup` 跑完、过滤已应用 |
| `sidecar/reloaded` | `plugins: int` | 热重载完成 |
| `sidecar/shutdown` | 无 | 关闭开始,**在 effect 之前** |
| `server/listening` | `mode: str`,`host: str`,`port: int` | HTTP 服务开始监听 |
| `server/request` | `method: str`,`path: str`,`status: int`,`ms: float` | 每个 HTTP 请求完成 |
| `server/route-error` | `method: str`,`path: str`,`error: str` | 插件路由处理器抛异常 |
| `rpc/connected` | 无 | RPC 循环开始 |
| `rpc/disconnected` | 无 | RPC 循环结束 |
| `agent/event` | `type: str`,`summary: str` | agent 循环的每个事件 |

#### `agent/event` 的 `type`

`forward_event()` 把 AgentScope 的事件归一成小写名放进 `type`:
`reply_start` / `model_call_start` / `model_call_end` / `text_block_delta` /
`tool_call_start` / `tool_call_delta` / `tool_call_end` / `tool_result_start` /
`tool_result_end` / `require_user_confirm` / `reply_end` 等。

**只有一个入口。** 不为每个事件类型单独开一个总线事件 —— 事件名归一曾经写了两份
并立刻漂移。

#### 订阅语义

**只有一种分发语义:广播。**

- `emit(event, **payload)`:所有订阅者都被调用,返回值忽略。单个订阅者抛错
  **不阻断**其余,记日志继续。
- 遍历的是 handler 列表的副本 —— 订阅者可以在回调里退订自己,那一轮的分发不受影响。
- 订阅用 `events.on(event, handler)`,返回退订函数。**不是装饰器。**

**没有「短路链」式的分发。** 底座没有任何一处会发那种事件,留着一个没人发的机制
只会让插件作者订上一个永远不会被调用的回调。

#### 不要订阅不存在的名字

`Telemetry` 只订阅**真的会发出**的事件。订阅一个永不触发的名字,计数永远是 0,
看起来像功能没生效 —— 排查方向直接被带偏。

---

## 7. 内置插件

两个插件把约定路径 `./SYSTEM.md` 与 `./skills` 接上。它们是**普通插件** ——
和用户插件走同一套机制,装在 `src/agentscope_sidecar/builtin_plugins/`,
优先级最低(用户插件可放一个同名插件覆盖它)。

### 7.1 `system_prompt`

读**运行目录**下的 `SYSTEM.md`(路径 `options.system_prompt_file`),作为提示词片段挂上。

```python
class _SystemFile(MiddlewareBase):
    def __init__(self, text: str) -> None:
        self.text = text

    async def on_system_prompt(self, agent, current_prompt: str) -> str:
        return f"{self.text}\n\n{current_prompt}"      # 放最前


def setup(agent, options, ctx) -> Effect:
    if options.system_prompt is not None:              # --system 内联优先
        text = options.system_prompt
    elif options.system_prompt_file.is_file():
        text = options.system_prompt_file.read_text(encoding="utf-8")
    else:
        return None                                    # 都没有 → 什么都不做

    section = _SystemFile(text)
    agent._system_prompt_middlewares.append(section)

    def cleanup() -> None:
        agent._system_prompt_middlewares.remove(section)

    return cleanup
```

**为什么用中间件而不是直接赋 `agent._system_prompt`**:直接赋值是**覆盖** ——  
后加载的插件会把它顶掉,而且底座自己赋的值也留不住。用中间件是**追加**,不覆盖别人。

**顺序**:`SYSTEM.md` 放最前(在 `current_prompt` 之前),插件片段追加在后。  
多个插件都往 `_system_prompt_middlewares` 追加时,按 append 顺序串行变换。

### 7.2 `skills`

读技能目录,挂到 `agent.toolkit` 上。

**`Toolkit` 没有公开的 `add_skill`。** 技能只能通过构造参数 `skills_or_loaders` 传入,  
最终落到 `tool_groups[0].skills_or_loaders` —— 一个**普通 list**。构造后追加就是改它。

```python
def setup(agent, options, ctx) -> Effect:
    dirs = [options.skills_dir, *options.extra_skills]
    existing = [p for p in dirs if p.is_dir()]
    if not existing:
        return None                                    # 一个都没有 → 什么都不做

    basic = next(g for g in agent.toolkit.tool_groups if g.name == "basic")
    loaders = [
        LocalSkillLoader(str(p), scan_subdir=True) for p in existing
    ]
    basic.skills_or_loaders.extend(loaders)

    def cleanup() -> None:
        for loader in loaders:
            basic.skills_or_loaders.remove(loader)

    return cleanup
```

**为什么追加到 `tool_groups[0]` 而不是新建 group**:`basic` 组是默认激活的  
(`state.tool_context.activated_groups`),新建的组默认不激活,技能说明不会出现。

**技能说明的注入点**:`Agent._get_system_prompt()` 里调  
`toolkit.get_skill_instructions(...)`,结果拼在 `agent._system_prompt` 之后、  
`_system_prompt_middlewares` 之前(§5.5)。

**读哪些目录**:`options.skills_dir`(默认 `./skills`)加上 `--skills` 给的额外目录。  
顺序即优先级,后者覆盖前者。

**`LocalSkillLoader(path, scan_subdir=True)` 的 `scan_subdir` 必须为 `True`** ——  
技能是 `skills/<name>/SKILL.md` 两层结构,不递归扫不到。

---

## 8. 归属追踪与过滤

插件直接改 `agent`,底座怎么知道"这个工具是谁加的"?

### 8.1 快照差分

loader 在每个插件的 `setup` **前后**对 `agent` 做快照,差分即归属:

```python
@dataclass(frozen=True)
class _Snapshot:
    tools: dict[str, int]          # 工具名 -> 出现次数(同一名字可以挂多个)
    middlewares: dict[str, int]    # 中间件类名 -> 出现次数
    overrides: dict[str, Any]      # Agent 构造参数 -> **对象本身**

def diff(self, before: _Snapshot) -> dict[str, list[str]]:
    """相对 before 新增了什么。{'tools': [...], 'middlewares': [...]}。"""
def changed_overrides(self, before: _Snapshot) -> list[str]: ...
```

**中间件没有名字,按 `type(mw).__name__` 归属**。同一插件挂同一个类的多个实例时
`diff` 会给出多条同名记录,回滚时逐条摘。

**`overrides` 存对象本身,不存 `id()`。** 只存 id 的话旧对象可能被回收,新对象恰好
复用同一个地址 —— 那时「换过对象」会被判成「没换」。存对象顺带把它钉住。

**不需要包装层** —— 插件照常用 `agent.toolkit.add_tool(...)`,底座只观察结果。

### 8.2 `inspect` 报告

```json
{
  "plugins": [
    {"id": "weather", "state": "loaded",
     "contributed": {"tools": ["get_weather"], "middlewares": ["Tag"]}}
  ],
  "tools": ["get_weather", "hello"],
  "tool_owners": {"get_weather": "weather"},
  "middlewares": ["Tag"],
  "unresolved": ["weather_service"],
  "routes": {"GET /stats": "dashboard"},
  "commands": {"report": "reports"},
  "services": {"catalog": "catalog", "agent": "<base>"},
  "agent_overrides": {
    "context_config": "compression_policy",
    "injection_config": "runtime_injection"
  }
}
```

`unresolved` 只统计 `LOADED` 的插件 —— 卸载后那条记录已经过期。

`agent_overrides` 按**身份比较**判定谁改过(`is not`):底座在 `setup` 前后各拍一次
快照,对象换了就是被改过。

### 8.3 `--tools` / `--middlewares` 过滤

在**所有 setup 跑完之后**应用:

- `--tools a,b`:把 `agent.toolkit` 里不在 `{a,b}` 的工具 `remove_tool`
- `--middlewares X,Y`:从七个桶里移除 `type(mw).__name__` 不在 `{X,Y}` 的中间件

**为什么在建 Agent 之后**:工具由插件挂上,过滤只能在其后。

---

### 8.4 回滚

`setup` 半途抛异常时,底座把已经发生的注册**全部撤销**:

```python
def _rollback(self, plugin_id: str, contributed: dict[str, list[str]]) -> None:
    """撤销一个插件在 Agent 与各注册表上留下的痕迹。"""
```

| 撤什么 | 怎么定位 |
| --- | --- |
| 工具 | 快照差分里的名字,从 `tool_groups[*].tools` 里摘 |
| 中间件 | 快照差分里的类名,从七个桶里各摘**一个**匹配的实例 |
| 路由 | `RouteRegistry.remove_owned(plugin_id)` |
| 命令 | `CommandRegistry.remove_owned(plugin_id)` |
| 服务 | `Context.remove_owned(plugin_id)` |

**为什么必须回滚。** 不回滚的话,插件的状态是 `failed` 而它挂上的工具能调、路由能
访问、服务还在容器里 —— 报告说「什么都没贡献」,实际是活的。这比直接失败更糟:
没有任何信号。`tests/e2e/agent/faulty/halfway.py` 就是盯这个的。

卸载插件时也走同一套(在 effect 之后,作为兜底)。摘中间件只摘**一个**匹配的实例 ——
同类型多实例时逐条摘;摘错对象比摘不掉更糟。

**回滚不了的:`agent.*` 构造参数覆盖。** 旧对象没有被保留,恢复不了。所以卸载后
`agent_overrides` 里仍然记着它 —— 那个覆盖确实还在生效,报告如实反映。

## 9. 运行选项

### 9.1 来源

**命令行 + 环境变量,没有配置文件。** 覆盖优先级:约定默认值 < 环境变量 < 命令行。

| 选项 | 环境变量 | 默认 |
| --- | --- | --- |
| `--mode` | `SIDECAR_MODE` | 有 task → `once`,否则 `console` |
| `--model` | `SIDECAR_MODEL` | 按凭据推断(§9.3) |
| `--plugins DIR`(可重复) | `SIDECAR_PLUGINS`(`os.pathsep` 分隔) | `./plugins` |
| `--system`(内联)/ `--system-file PATH` | — | `./SYSTEM.md` |
| `--skills PATH`(可重复) | — | `./skills` |
| `--permission` | `SIDECAR_PERMISSION` | `default` |
| `--host` | `SIDECAR_HOST` | `127.0.0.1` |
| `--port` | `SIDECAR_PORT` | `8000` |
| `--session-dir PATH` | `SIDECAR_SESSION_DIR` | `./var/sessions`(不生效) |
| `--log-level` / `--verbose` / `--quiet` | `SIDECAR_LOG_LEVEL` | `INFO` |

**`--plugins` 与 `SIDECAR_PLUGINS` 不叠加** —— 给了前者就忽略后者。`--plugins`
本身可重复,不需要环境变量来凑多个。

实际插件目录顺序是 `[内置, ...--plugins, ./plugins]` —— `./plugins` 恒在最后、
优先级最高、不可移除但可为空。**有序,后者覆盖前者**;同 id 时高优先级完全取代
低优先级,被覆盖者记 `SHADOWED` 并出现在 `inspect` 里。

多值选项要**归一**:`--tools ""` 是「明确要求一个都不留」,与「没给 `--tools`」
不同。前者是空元组,后者是 `None`。

没有第四层。任何「需要改配置文件才能表达」的需求,都是设计缺陷,不是功能缺口。

### 9.2 `RuntimeOptions`

```python
@dataclass(frozen=True)
class RuntimeOptions:
    # 路径
    base_dir: Path
    plugin_dirs: tuple[Path, ...]
    skills_dir: Path
    extra_skills: tuple[Path, ...]        # 新增:--skills 可重复
    system_prompt_file: Path
    system_prompt: str | None
    session_dir: Path                     # [未实现]

    # 装配
    model: str | None
    tools: tuple[str, ...] | None
    middlewares: tuple[str, ...] | None
    permission: PermissionModeName

    # 运行
    mode: Mode
    host: str
    port: int
    task: str | None
    reload: bool
    log_level: str
    extensions: tuple[Path, ...]
```

**没有 `model_kwargs`。** 模型参数由 provider 构造函数自己取;插件要改就换 `agent.model`。**没有 `max_iters`。** 轮数由插件设 `agent.react_config`。

### 9.3 默认模型

建 Agent 前底座先定引用、再造实例。两步分开是为了让引用能进报告:

```python
def resolve_model_ref(options: RuntimeOptions, *,
                      env: Mapping[str, str] | None = None) -> str:
    """定下 ``provider/model``。显式 > 唯一凭据 > 免凭据兜底。"""

def build_model(reference: str, *,
                env: Mapping[str, str] | None = None) -> ChatModelBase:
    """按引用造实例。引用格式非法或缺凭据时抛 ConventionError。"""

def split_reference(reference: str) -> tuple[str, str]: ...
```

| provider    | 凭据环境变量              | 模型类                  | 默认模型                  |
| ----------- | ------------------- | -------------------- | --------------------- |
| `dashscope` | `DASHSCOPE_API_KEY` | `DashScopeChatModel` | `qwen-plus`           |
| `openai`    | `OPENAI_API_KEY`    | `OpenAIChatModel`    | `gpt-4o`              |
| `deepseek`  | `DEEPSEEK_API_KEY`  | `DeepSeekChatModel`  | `deepseek-chat`       |
| `anthropic` | `ANTHROPIC_API_KEY` | `AnthropicChatModel` | `claude-sonnet-4-…`   |
| `moonshot`  | `MOONSHOT_API_KEY`  | `MoonshotChatModel`  | `moonshot-v1-8k`      |
| `mock`      | 无                   | `ScriptedChatModel`  | `scripted`            |

`PROVIDER_DEFAULT_MODEL` 是「不指定模型时用哪个」的唯一来源。

**凭据由底座从环境变量取。** AgentScope 的凭据类不读环境变量
(`DashScopeCredential.api_key` 是必填字段,类里没有 `getenv`),必须显式传。

推断顺序:

1. `--model` / `SIDECAR_MODEL` 给了 → 直接用
2. 恰好一个 provider 的凭据环境变量已设置 → 用它的默认模型
3. 多个 → 抛 `ConventionError`,不猜
4. 零个 → 回退 `mock/scripted` 并**打告警** —— 避免生产环境忘了设密钥而静默跑在
   离线模型上
5. 引用里的 provider 不在表里 → 抛 `ConventionError`,提示用插件换 `agent.model`

**插件要换模型**:在 `setup` 里 `agent.model = ...`。`--model` 只认内置 provider ——
自定义 provider 由插件自己读环境变量构造。

#### `ScriptedChatModel` 的脚本来源

离线模型按脚本回放,全部测试靠它,不需要任何 API 凭据。来源按优先级:

1. 环境变量 `MOCK_LLM_SCRIPT`(JSON 数组)
2. 包目录下的 `config.json` 的 `script` 字段

每步消耗一次模型调用;脚本耗尽后重复最后一个 `text` 步。步格式:

```json
{"tool_call": {"name": "get_weather", "args": {"city": "北京"}}}
{"text": "北京当前晴,25℃。"}
{"thinking": "先查天气"}
```

**文本按 6 字符切块,全部以非终块发出,最后补一个携带完整文本的终块。** 只发一个
终块会让 `TEXT_BLOCK_DELTA` 全部丢失;终块只带最后一片会让最终消息被截断(§11.7)。

---

## 10. 文件布局

```
src/agentscope_sidecar/
├── __init__.py            导出面
├── __main__.py            python -m agentscope_sidecar
├── app.py                 Sidecar:启动、报告、过滤、重载、关闭
├── cli.py                 argparse + 子命令(inspect / list / check)
├── context.py             服务容器 + 惰性代理 + 插件视图
├── convention.py          CliOptions / RuntimeOptions / resolve()
├── discover.py            非递归扫描、模块导入、插件名校验
├── effects.py             Effect 类型与 run_effect
├── errors.py              异常分类
├── events.py              EventBus(单一广播语义)
├── inject.py              签名依赖注入
├── loader.py              PluginLoader:加载时序、快照差分、卸载、重载
├── models.py              provider 表与 build_model()
├── mock_model.py          内置离线脚本模型
├── reload.py              文件监听(标准库轮询,可选 watchfiles)
├── builtin_plugins/
│   ├── system_prompt.py   读 ./SYSTEM.md
│   └── skills.py          读 ./skills/ 与 --skills
├── modes/
│   ├── __init__.py
│   ├── _wire.py           线格式(json / rpc 共用)
│   ├── once.py
│   ├── console.py
│   ├── json_mode.py
│   ├── rpc.py
│   └── http_mode.py
└── services/
    ├── __init__.py
    ├── registry.py        RouteRegistry / CommandRegistry
    └── telemetry.py       事件计数与时间线
```

**新模式的接法**:在 `modes/` 加一个文件,在 `app.py` 的 `_dispatch_mode()` 里登记一行。
各模式互不依赖。

---

## 11. 运行模式

### 11.1 选择与默认

| 来源 | 优先级 |
| --- | --- |
| `--mode` | 最高 |
| `SIDECAR_MODE` | 中 |
| 推断:有 task → `once`,否则 `console` | 最低 |

`MODES = ("once", "console", "json", "rpc", "http")`。未知模式在 `resolve()` 里
就抛 `ConventionError`,不进 boot。

**stdout 是协议通道。** 五种模式里只有 `once` / `console` 往 stdout 写结果文本,
其余写结构化协议帧;日志一律走 stderr。插件往 stdout 打印会污染协议。

`_dispatch_mode()` 是唯一的分发表,每种模式一个模块,互不依赖(§10)。

### 11.2 `once`

跑一个 task,结果文本流式写到 stdout,退出。返回码 0。

没有 task → `ConventionError("once 模式需要一个任务参数")`,CLI 退出码 2。

工具调用只往 **stderr** 打一行 `[tool] <name>`,不混进结果文本。

### 11.3 `console`

长驻 REPL,是**唯一**的交互模式(`is_interactive()` 只认它)。

stdin 按行读,空行或 EOF 退出。提示符 `> ` 与说明文字写 stderr —— stdout 只有回答。

### 11.4 `json`

事件流 JSONL 到 stdout,最后一行固定是 `{"type": "result", ...}`。

- 事件行由 `event_to_wire()` 生成(§11.7)。
- 最后一行的 `content` 来自 `message_to_wire()`,即 `yield_final_msg=True` 产出的
  那个 `Msg`。
- 中途抛异常时写一行 `{"type": "error", "error": ...}` 并返回 1。
- 没有 task → `ConventionError`,CLI 退出码 2。

### 11.5 `rpc`

JSON-RPC 2.0 over stdio,一行一个 JSON 对象。

| 方法 | `params` | `result` |
| --- | --- | --- |
| `ping` | — | `{"pong": true}` |
| `initialize` | — | `{model, mode, permission, tools, middlewares, plugins, routes}` |
| `prompt` | `{"text": str}` | `{"reply_id": str}`;受理后事件以 `event` 通知推送 |
| `cancel` | `{"reply_id": str}` | `{"cancelled": bool}` |
| `shutdown` | — | `{}`,随后停止读循环 |

通知(无 `id`):`event` / `plugin`。`event` 的载荷是 `event_to_wire()` 的结果,
`plugin` 的载荷是插件状态事件的原始 `params`。

| 错误码 | 含义 |
| --- | --- |
| `-32700` | JSON 无法解析 |
| `-32600` | 请求不是对象 |
| `-32601` | 未知方法 |
| `-32602` | `params` 非法(如 `text` 不是非空字符串) |
| `-32001` | 已有进行中的 `prompt` —— 同一 session 不允许并发 |

stdin 关闭等同 `shutdown`。退出时取消全部在跑的 session 并等它们收尾,再发
`rpc/disconnected`。

`plugin` 通知只订阅**真的会发出**的事件(§6.6):`sidecar/plugin-loaded` /
`-failed` / `-shadowed` / `-unloaded`。

**写锁必须是本次运行的局部对象。** 做成模块级全局会在第二次 `run()` 时复用
绑定到上一个事件循环的锁,`asyncio` 直接抛 `bound to a different loop`。

### 11.6 `http`

Starlette + uvicorn。**默认绑 `127.0.0.1`**,不绑 `0.0.0.0`。

内置路由(路径是完整路径,底座不加前缀):

| 路由 | 方法 | 说明 |
| --- | --- | --- |
| `/health` | GET | `{"status": "ok", "plugins": {"loaded": n, "failed": n}}` |
| `/info` | GET | `sidecar.report()` 全文 |
| `/prompt` | POST | body `{"text": str}`;返回 `{"reply_id": str}`,或 `Accept: text/event-stream` 时直接返回 SSE 流 |
| `/events/{reply_id}` | GET | SSE 事件流 |
| `/cancel` | POST | body `{"reply_id": str}`;返回 `{"cancelled": bool}` |

插件路由与内置路由挂在同一个 app 上。`(path, method)` 与内置冲突时**启动即失败**
(`DeclarationError`),不静默覆盖。

启动前先探测端口:被占用则 stderr 打印一行并**返回退出码 3**。`/prompt` 在有进行中
的回复时返回 409。

`reply_id` 不存在时 `/events/{id}` 不返回 404,而是返回一条立刻结束的 `event: error`
流 —— 客户端拿到 `reply_id` 与订阅之间可能有时差,给 404 会让正常时序也报错。

### 11.7 线格式与流式分片

`modes/_wire.py` 是 `json` / `rpc` / `http` 共用的线格式层。

```python
def event_type(event: Any) -> str: ...          # 归一成小写
def event_to_wire(event: Any, reply_id: str | None = None) -> dict[str, Any]: ...
def message_to_wire(msg: Any) -> dict[str, Any]: ...
def now_iso() -> str: ...                       # UTC,毫秒精度,以 Z 结尾
```

`event_to_wire` 先 `model_dump(mode="json")`,再丢掉 `_DROP = {"id", "created_at",
"metadata"}` 三个噪声字段,最后写入 `type` / `ts` / `reply_id`。

**事件名归一只有这一处实现。** AgentScope 的 `EventType` 成员名是大写
(`REPLY_START`),而 `event.type` 也可能是构造时直接给的字符串,两种都要处理。
归一逻辑曾经写了两份并立刻漂移。

**流式分片语义**:非终块全部发出;终块携带**完整文本**,不是增量。只发一个
`is_last=True` 的块时**不产生** `TEXT_BLOCK_DELTA` —— 所以「模型没输出」和
「模型一次输出完」在事件流里长得不一样,别混。

### 11.8 权限模式与非交互

```python
PERMISSION_MODES = ("default", "accept_edits", "explore", "bypass", "dont_ask")
INTERACTIVE_MODES = ("console",)
```

只有 `console` 有用户可确认。其余模式下 `ASK` **无法满足** —— 工具不会执行,整轮
就此结束,而调用方只看到回答莫名其妙地短了。`forward_event()` 在收到
`require_user_confirm` 且当前模式非交互时打一条 warning,列出被拦下的工具名并提示
用 `--permission` 调整。

**权限规则的 key 是工具名,不是插件名。** 写成插件名规则永远匹配不上,而且没有
任何提示 —— 引擎找不到规则就按模式默认值走。

**`bypass` 不跳过 `ask` 规则。** `_check_bypass` 的顺序是
deny → ask → 只读 → 工具自己的判定 → allow 规则 → 放行。要对比「模式差异」,
得用一个**没有任何规则**的工具。

### 11.9 热重载

`--reload` 起一个后台任务监听插件目录(`reload.watch()`:有 `watchfiles` 就用它,
否则标准库轮询,1 秒一轮)。文件增删改都会触发。

```
卸载全部插件 → 清模块缓存 → 重建 Agent → 重新加载 → 重发插件状态 → 重应用过滤
→ 发 sidecar/reloaded
```

**必须重建 Agent。** 它持有旧插件贡献的中间件与工具实例,复用等于把旧对象留在
新的一轮里。

模块缓存按 `sidecar_plugin_` 前缀整段清 —— 只清顶层插件模块不够:目录形态插件的
私有辅助模块注册为 `sidecar_plugin_<id>.<sub>`,不清的话改了 `helper.py` 不生效。

重载失败时记日志并**保持原有插件继续运行**,不中断服务。

监听目录是 `[BUILTIN_PLUGINS_DIR, *plugin_dirs]`。

### 11.10 异常层次

```python
class SidecarError(Exception): ...          # 基类。CLI 只捕它
class ConventionError(SidecarError): ...    # 约定推断失败:模型引用非法或缺凭据、选项非法、任务为空
class PluginImportError(SidecarError): ...  # 模块导入失败(语法错误、import 错误)
class DeclarationError(SidecarError): ...   # 注册非法:路由/命令/服务重名、路径非法、setup 签名非法
class ServiceNotFound(SidecarError): ...    # 惰性代理被使用时服务仍不存在
```

**只保留真正会被抛出的类型。** 定义但从不抛出的异常比没有更糟 —— 调用方会以为
可以捕获它。

插件内部的异常**不冒泡到底座**:`setup` 抛异常记 `FAILED` + 回滚(§8.4),
agent 循环抛异常写进事件流的 `error` 行。`SidecarError` 只覆盖底座自己的失败。

CLI 退出码:

| 码 | 含义 |
| --- | --- |
| 0 | 正常 |
| 1 | 启动失败 / 运行失败 / `check` 发现失败插件 |
| 2 | 用法错误(该给任务没给) |
| 3 | `http` 端口被占用 |

---

## 12. 测试策略

### 12.1 分层

- **机制测试** `tests/test_*.py` —— 单个机制的边界。测试内拼插件字符串。
- **夹具测试** `tests/e2e/` —— `agent/` 下 13 个真实插件文件,走完整路径。

预期结果集中在 `tests/e2e/expected.py`,加一个夹具插件不用翻八个文件。

### 12.2 覆盖

`pytest -q` **559 项**全绿,覆盖率 **行 96.8% / 分支 94.6%**(阈值各 80%,
由 `scripts/coverage_gate.py` 强制)。

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_context.py` | 服务容器、惰性代理、插件视图、归属 |
| `tests/test_inject.py` | 六条解析规则、注解解析、错误消息 |
| `tests/test_loader.py` | 导入 → setup → effect、快照差分、卸载、重载 |
| `tests/test_events.py` | 广播语义、退订、抛错隔离 |
| `tests/test_events_catalog.py` | 事件目录:文档 ↔ 代码双向比对 |
| `tests/test_wire.py` | 线格式的三种输入形态 |
| `tests/test_doc_references.py` | 源码 § 引用 ↔ 设计文档章节一致性 |
| `tests/test_discover.py` | 非递归扫描、`_` 前缀、名字校验 |
| `tests/test_convention.py` | 选项解析、模型推断、默认值 |
| `tests/test_models.py` / `test_mock_model.py` | provider 表与离线脚本模型 |
| `tests/test_builtin_plugins.py` | `SYSTEM.md` 与 `skills/` 插件 |
| `tests/test_http_mode.py` | 事件队列、路由包装、启动流程 |
| `tests/test_cli.py` / `test_modes.py` | 子命令、五种模式、协议面 |
| `tests/test_services.py` / `test_app.py` / `test_effects.py` | 注册表、遥测、生命周期、effect |
| `tests/test_examples.py` | `examples/` 下每个示例都真跑一遍 |

### 12.3 e2e 夹具

`tests/e2e/agent/` 是一份真实可跑的项目目录,测试跑在它的副本上:

| 目录 | 内容 |
| --- | --- |
| `plugins/` | 13 个插件(12 LOADED + 1 SKIPPED):单文件、目录形态、无 setup、`_` 前缀 |
| `faulty/` | 12 个故意写坏的插件,单独当 `plugins/` 用 |
| `optional/` | 惰性依赖与可选依赖 |
| `shared/{base,biz}` | 两个插件目录,测优先级与覆盖 |

夹具本身也受约束:插件名不能是 `policy` / `config` 这类通用词(`tests/helpers.py`
的 `GENERIC_PLUGIN_NAMES`),报告里的 id 必须与文件名一致 —— 用户靠这个定位文件。

**只断言 `state == failed` 是不够的** —— `NameError` 也会让插件 FAILED。
每条故障断言都同时盯住错误消息里的关键词。

---

## 13. 非目标

**加一个机制前先问「不加它,用签名 + `ctx` 能不能写出来」。能,就不加。**

不做:

- `inject = [...]` / `@Autowired` / `@Qualifier` / `@Primary`
- setter 注入、字段注入
- scope(singleton / prototype / request)
- 循环依赖检测、拓扑排序、启动期依赖图校验
- `@PostConstruct` / `@PreDestroy`
- 按注解自定义解析器
- 进程外插件宿主与插件签名审计(除非要隔离不可信插件)
- 会话状态落盘(`RuntimeOptions.session_dir` 已解析但**无消费者**)

需要这些的场景用 `ctx.get` / `ctx.require` 手写 —— 显式比框架聪明好排查。
