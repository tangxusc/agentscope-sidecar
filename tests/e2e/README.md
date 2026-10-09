# e2e 测试夹具

`agent/` 是一份**真实可跑的项目目录**,不是拼出来的字符串。测试跑在它的副本上,
所以插件往自己目录写日志不会弄脏夹具。

## 目录

```
agent/
├── SYSTEM.md                 # 系统提示词(内置 system_prompt 插件读它)
├── skills/code-review/SKILL.md
├── plugins/                  # 13 个插件(12 个 LOADED + 1 个 SKIPPED)
│   ├── weather/              #   目录形态:带配套模块 api_client.py
│   ├── audit.py              #   中间件 + 提示词片段
│   ├── dashboard.py          #   3 条 HTTP 路由
│   ├── reports.py            #   工具 + 命令 + 自带清理
│   ├── permissions.py        #   权限规则
│   ├── lifecycle.py          #   11 个事件订阅
│   ├── catalog.py            #   提供服务
│   ├── consumer.py           #   按名称注入服务
│   ├── agent_tuning.py       #   改 Agent 构造参数
│   ├── danger.py             #   被 deny 规则挡住的工具
│   ├── notes.py              #   无任何规则的普通工具
│   ├── slow.py               #   慢工具
│   ├── scratch.py            #   没有 setup → SKIPPED
│   └── _wip.py               #   `_` 前缀 → 移出插件集
├── faulty/                   # 12 个故意写坏的插件(单独当 plugins/ 用)
├── optional/                 # 惰性依赖与可选依赖
└── shared/                   # 两个插件目录,测优先级与覆盖
    ├── base/                 #   低优先级
    └── biz/                  #   高优先级
```

## 夹具

| 夹具 | 内容 | 用途 |
| --- | --- | --- |
| `e2e` | 整份 `agent/` | 主场景 |
| `faulty` | `faulty/` 当 `plugins/` | 故障隔离矩阵 |
| `optional` | `optional/` 当 `plugins/` | 惰性依赖 |
| `shared` | 空 `plugins/` + `shared/{base,biz}` | 多目录优先级 |
| `bare` | 只有空 `plugins/` | 测试自己写插件 |

除 `e2e` 外都**不加载主插件** —— `./plugins` 恒在插件目录列表末尾,不隔离的话
那 13 个插件会把断言搅浑。

## 测试文件分工

| 文件 | 覆盖 |
| --- | --- |
| `test_plugins.py` | 加载状态、归属、卸载、内置插件优先级 |
| `test_agent.py` | 提示词、技能、中间件、构造参数、事件订阅 |
| `test_multidir.py` | 多目录优先级、覆盖、跨目录服务、`-e`、非法插件名 |
| `test_faulty.py` | 故障隔离矩阵(状态 **和** 错误消息) |
| `test_dependencies.py` | 签名注入、惰性代理、按类型注入、服务冲突 |
| `test_events_permissions.py` | 事件广播、遥测、权限模式、`--tools` 过滤 |
| `test_http.py` | 内置路由、插件路由、SSE、端口占用 |
| `test_reload.py` | 热重载、模块缓存、文件监听、`--reload` |

`expected.py` 集中放预期结果 —— 加一个插件不用翻八个文件。

**只断言 `state == failed` 是不够的**:`NameError` 也会让插件 FAILED。每条故障
断言都同时盯住错误消息里的关键词。
