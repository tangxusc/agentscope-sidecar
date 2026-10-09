# agentscope-sidecar

基于 AgentScope 2.x 的「运行时与插件分离」Agent 底座。

底座负责 agent 循环、模型接入、权限、遥测与运行模式;业务能力以**插件**形式放在插件目录里,
由底座自动发现并加载。设计规格见 [`docs/design.md`](docs/design.md)。

## 安装

```bash
pip install -e ".[dev]"
```

运行时依赖只有 `agentscope`。HTTP 模式用的 `starlette` / `uvicorn` 是它的传递依赖,
不额外引入。

## 快速开始

```bash
mkdir -p my-agent/plugins && cd my-agent

cat > plugins/hello.py <<'PY'
from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse


async def hello(name: str) -> ToolResponse:
    """向某人问好。

    Args:
        name (str): 名字
    """
    return ToolResponse(content=[TextBlock(type="text", text=f"你好,{name}!")])


async def setup(agent: Agent) -> None:
    await agent.toolkit.add_tool(FunctionTool(hello, is_read_only=True))
PY

sidecar "打个招呼"
```

没有配置文件,没有清单文件 —— 放进 `plugins/` 就生效。

## 插件怎么写

**唯一入口是模块级的 `async def setup(...)`。** 参数按名字从容器里注入(见下),
底座先把 Agent 建好再交给插件改:

```python
# plugins/weather/__init__.py
from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse


async def get_weather(city: str) -> ToolResponse:
    """查询指定城市的实时天气。

    Args:
        city (str): 城市名,例如「北京」
    """
    return ToolResponse(content=[TextBlock(type="text", text=f"{city} 晴")])


async def setup(agent: Agent, ctx, events) -> None:
    await agent.toolkit.add_tool(FunctionTool(get_weather, is_read_only=True))

    def on_ready(**_: object) -> None:
        ctx.logger.info("weather 就绪")

    events.on("sidecar/ready", on_ready)      # 返回退订函数,不是装饰器
```

`setup` 直接对拿到的对象做事。常见动作:

| 想做什么 | 怎么做 |
| --- | --- |
| 挂工具 | `await agent.toolkit.add_tool(FunctionTool(fn, is_read_only=True))` |
| 挂中间件 | `agent._model_call_middlewares.append(mw)`(七个桶,见设计文档 §5.4) |
| 改系统提示词 | 写一个 `on_system_prompt` 中间件并 append 到 `_system_prompt_middlewares` |
| 改模型 | `agent.model = OtherModel(...)` |
| 改构造参数 | `agent.context_config = ContextConfig(...)` 等 |
| 加权限规则 | `agent.state.permission_context.deny_rules[...] = [...]` |
| 注册路由 | `routes.add("GET", "/path", handler)` |
| 注册命令 | `commands.add("name", handler)` |
| 订阅事件 | `events.on("sidecar/ready", handler)`(返回退订函数) |
| 提供服务 | `ctx.provide("name", value)` |

**没有装饰器。** 能力由「你拿到哪个对象」决定,不由标记决定。

**`setup` 必须是 `async def`** —— `Toolkit.add_tool` / `remove_tool` 是异步的。
写成同步的会直接判 `FAILED`,报错说明原因。

**`setup` 半途抛异常不会留下垃圾。** 底座会回滚它已经做的注册(工具、中间件、
路由、命令、服务),然后记 `FAILED` 继续下一个插件。

### 依赖注入

`setup` 的参数列表就是依赖声明。按参数名解析,名字对不上再看类型注解:

```python
async def setup(agent, options, events, catalog) -> None: ...   # 全按名称
async def setup(agent: Agent, store: SomeType) -> None: ...     # 按类型兜底
```

| 条件 | 注入 |
| --- | --- |
| 名字是 `ctx` | 该插件自己的容器视图(`ctx.id` / `ctx.dir` 才是对的) |
| 名字以 `_` 开头 | **不注入**,用默认值(没有默认值则报错) |
| 容器里有这个名字 | 真实对象 |
| 名字没有,但类型注解在容器里恰好一个 | 按类型注入 |
| 名字没有,类型匹配到多个 | **报错**,不猜 |
| 有默认值 | 用默认值 |
| 其余 | **惰性代理** —— 首次使用才解析,加载顺序无关 |

拿不到的服务不会让插件失败,而是记进 `inspect` 报告的 `unresolved` 字段。

### 清理

`setup` 的返回值是 effect —— **无参数**的函数(同步或 `async` 都可以),或 `None`:

```python
async def setup(agent, commands) -> Effect:
    await agent.toolkit.add_tool(FunctionTool(make_report, is_read_only=True))
    commands.add("report", on_report)

    def cleanup() -> None:
        commands.remove("report")

    return cleanup
```

底座退出时**逆序**调用全部 effect。单个 effect 抛异常不阻断其余,记进日志继续往下调。

**effect 不是唯一的清理途径。** 卸载插件时底座还会按归属兜底回滚 —— 摘掉它贡献的
工具、中间件、路由、命令、服务。effect 写漏了不会留下垃圾。

## 目录约定

运行 `sidecar` 时所在的**当前工作目录**是基准,所有相对路径相对它解析。
**四条路径全部可选** —— 缺了静默跳过,不报错。空目录也能启动(只剩内置插件)。

| 路径 | 用途 | 覆盖方式 | 状态 |
| --- | --- | --- | --- |
| `SYSTEM.md` | 系统提示词(存在即用) | `--system`(内联文本)/ `--system-file PATH` | 生效 |
| `plugins/` | 插件目录 | `--plugins DIR`(可重复)/ `SIDECAR_PLUGINS` | 生效 |
| `skills/` | 技能目录 | `--skills PATH`(可重复) | 生效 |
| `var/sessions/` | 会话状态 | `--session-dir PATH` / `SIDECAR_SESSION_DIR` | **[未实现]** |

`SYSTEM.md` 与 `skills/` 都由**内置插件**读 —— 它们和你的插件走同一套机制,
只是底座替你装好了。

```
my-agent/
├── SYSTEM.md
├── plugins/
│   ├── weather.py           # 单文件形态
│   └── audit/               # 目录形态
│       ├── __init__.py      # 唯一入口
│       └── rules.json       # 私有资源,不参与扫描
├── skills/
│   └── deploy/
│       └── SKILL.md
└── var/sessions/            # 见下,目前不生效
```

**没有 agent 目录这个概念。** 一套 agent = 一个工作目录 + 若干插件目录。

**系统提示词三选一,按优先级:** `--system` 内联文本 > `--system-file` 指定的文件 >
`./SYSTEM.md`。给了高优先级的,低优先级那个**根本不读**(不是拼接)。

插件贡献的提示词片段与上面那份是**拼接**关系 —— 用 `on_system_prompt` 中间件追加,
先放文件内容,再按中间件加入顺序拼。

### 三条容易踩的约定

**插件目录内部一律不扫描。** 只有插件目录的**顶层条目**算插件,目录形态只看 `__init__.py`。
配套文件(数据、模板、子模块)天然安全。反过来:**`plugins/audit/SYSTEM.md` 不会被读** ——
插件想贡献提示词要写中间件,放个 `SYSTEM.md` 进去是静默无效的。

**`./plugins` 不是唯一的插件目录。** 实际顺序是:

```
[内置插件目录, ...--plugins 指定的, ./plugins]
   最低优先级                        最高优先级
```

有序,**后者覆盖前者**。`./plugins` 恒在最后、优先级最高、不可移除,但可以为空。

**`var/sessions/` 目前不生效。** 路径会解析进 `RuntimeOptions.session_dir`,但底座没有
把它交给 AgentScope —— 会话状态还没有落盘。设置 `--session-dir` 不会有任何效果。

## 多插件目录

```bash
sidecar --plugins ~/agentscope-plugins/base --plugins ~/agentscope-plugins/sales "任务"
SIDECAR_PLUGINS=~/agentscope-plugins/base:~/agentscope-plugins/sales sidecar "任务"
# plugin_dirs = [内置, ~/.../base, ~/.../sales, ./plugins]
```

有序,**后者覆盖前者**。同 id 时高优先级完全取代低优先级,被覆盖者记 `SHADOWED`
并在 `inspect` 里列出。

**`--plugins` 与 `SIDECAR_PLUGINS` 不叠加** —— 给了 `--plugins` 就忽略环境变量
(`--plugins` 可重复,不需要环境变量来凑多个)。`--plugins` 与 `./plugins` 是叠加的:
`./plugins` 恒在最后。

## 运行模式

```bash
sidecar "任务"                         # once:文本到 stdout
sidecar                                # console:交互 REPL
sidecar --mode json "任务" > ev.jsonl  # json:事件流 JSONL
sidecar --mode rpc                     # rpc:JSON-RPC over stdio
sidecar --mode http --port 8000        # http:HTTP 服务 + 插件路由
```

`json` / `rpc` 模式下 **stdout 只有协议帧,日志全部走 stderr**。

## 模型

```bash
SIDECAR_MODEL=dashscope/qwen-plus DASHSCOPE_API_KEY=sk-xxx sidecar "任务"
```

内置 provider:`dashscope` / `openai` / `deepseek` / `anthropic` / `moonshot` / `mock`。
未指定时按「已设置的凭据环境变量」推断;都没设时回退到免凭据的 `mock`(会打告警)。
多个凭据同时存在时报错而不是猜。

**插件可以换掉模型** —— `--model` 表达不了的自定义 provider,在 `setup` 里
`agent.model = MyModel(...)`。

底座自带一个离线脚本模型,因此**测试不需要任何 API 凭据**:

```bash
MOCK_LLM_SCRIPT='[{"tool_call":{"name":"hello","args":{"name":"世界"}}},{"text":"完成。"}]' \
  sidecar "任务"
```

## 运维接口

```bash
sidecar inspect          # 加载报告:插件状态、归属、未解析依赖、Agent 覆盖、路由
sidecar inspect --json   # 同构 JSON;HTTP 模式下 GET /info 返回同一份
sidecar check            # 校验,有插件 FAILED 则退出码 1
sidecar list             # 列出插件与状态
```

`inspect` 里的 `tool_owners` 告诉你每个工具是哪个插件挂的 —— 插件直接改 Agent,
归属靠快照差分得出,不需要插件自己申报。

## 插件发现规则

`plugins/` 顶层是**插件命名空间**:

| 顶层条目 | 处理 |
| --- | --- |
| `*.py` | 插件(单文件形态) |
| 含 `__init__.py` 的目录 | 插件(目录形态,内部文件不被扫描) |
| `_` 开头 | 移出插件集(私有、临时、停用) |
| `__pycache__` / `*.pyc` | 忽略 |
| 其他文件 / 纯资源目录 | 忽略 |

**没有 `setup` 的插件记 `SKIPPED`,不是失败** —— 它是诊断信号,会列在报告里。

**插件名必须是合法的 Python 标识符。** 插件文件就是 Python 模块,而且 id 会进入
模块名、`sys.modules` 的键和日志名。`my-plugin.py` 这类名字会被归一化成
`my_plugin`,与 `my_plugin.py` **撞同一个模块键** —— 两个插件共用一份模块对象,
两边都报 `loaded` 但有一个的模块已经丢了。这类名字会被跳过并告警。

```
my-plugin.py  →  ❌ 用 my_plugin.py
123abc.py     →  ❌
class.py      →  ❌
Weather.py    →  ✅(大写只是不符合 PEP 8)
```

**目录形态是完整形态** —— 配套模块、数据、模板都放插件目录内,只有 `__init__.py` 被导入。

停用插件就是给它加 `_` 前缀。

## 权限

工具可以声明「需要用户确认」。**只有 `console` 模式有用户可确认** —— 其余模式下
`ASK` 无法满足,工具不会执行,底座会打告警告诉你该怎么调:

```
WARNING sidecar | 工具 ['danger'] 需要用户确认,但 json 模式无法交互 ——
        本次调用已跳过。用 --permission accept_edits|dont_ask|bypass 调整策略。
```

```bash
sidecar --mode http --permission dont_ask      # 无人值守:ASK 转 DENY
sidecar --mode http --permission accept_edits  # 允许工作目录内的编辑
sidecar --mode http --permission bypass        # 跳过安全检查(仍受 deny 规则约束)
```

插件直接往 Agent 的权限上下文里加规则,`bypass` 下 `deny` 仍然生效:

```python
from agentscope.permission import PermissionBehavior, PermissionRule


async def setup(agent) -> None:
    context = agent.state.permission_context
    context.deny_rules.setdefault("Bash", []).append(PermissionRule(
        tool_name="Bash", rule_content="rm -rf",
        behavior=PermissionBehavior.DENY, source="my-plugin",
    ))
```

**规则的 key 是工具名,不是插件名。** 写错了不会有任何提示 —— 引擎找不到规则,
按模式默认值走。

**`rule_content` 只对重写了 `match_rule` 的工具有效**(Bash / Read / Write / Edit /
Glob / Grep)。自定义函数工具只能写 `rule_content=None`,那是工具名级别的规则。

## 热重载

```bash
sidecar --mode http --reload --port 8000
```

改插件文件后自动重载 —— 逆序调全部 effect、清模块缓存(含插件私有子模块)、
**重建 Agent**(它持有旧插件的中间件与工具实例)、重新加载。**重载失败不会把服务搞挂**。

文件监听用标准库轮询实现,不引入新依赖;装了 `watchfiles` 会自动改用它。

## 测试

```bash
pytest -q
```

559 项测试,全部跑真实进程(CLI 子进程、RPC stdio、HTTP 服务),不需要任何凭据。

### 覆盖率

```bash
COVERAGE_PROCESS_START=$PWD/pyproject.toml \
  pytest -q --cov=agentscope_sidecar --cov-branch
python scripts/coverage_gate.py
```

当前 **行 96.8% / 分支 94.6%**,阈值是各 80%。

两个和覆盖率有关的坑,都已在配置里处理:

**子进程要单独上报。** 大量测试跑的是真实子进程,父进程的 coverage 看不到它们 ——
不打通这一层,`cli.py` 与 `modes/*` 会一直显示 0%,像是没测过。
`tests/_coverage_boot/sitecustomize.py` 负责这件事,只在 `COVERAGE_PROCESS_START`
已设置时生效,普通 `pytest -q` 不受影响。

**常驻服务要能落盘。** HTTP / RPC 服务由测试用 SIGTERM 结束,coverage 默认来不及
写数据 —— `[tool.coverage.run] sigterm = true` 是必须的。

`scripts/coverage_gate.py` 把行与分支**分开**卡阈值:coverage 自带的 `fail_under`
只看合并值,行 95% 而分支 60% 时照样通过。

## 尚未实现

- **`var/sessions/` 会话落盘** —— `session_dir` 已解析但没有消费者,会话状态不持久化
- 进程外插件宿主(隔离不可信插件才需要)
- 插件签名与审计
