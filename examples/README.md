# 可运行示例

每个目录都是一个独立的 agent 工作目录。`cd` 进去就能跑,不需要 API 凭据 ——
底座在没有模型凭据时会回退到内置的离线脚本模型。

```bash
cd examples/01-hello
sidecar "跟世界打个招呼"
```

| 目录 | 演示 | 读什么 |
| --- | --- | --- |
| `01-hello` | 最小插件:一个工具 + 一个 effect | `setup` 的返回值 |
| `02-middleware` | 中间件与提示词片段,两个 hook 签名不同 | `_*_middlewares` 七个桶 |
| `03-agent-config` | 直接改 `Agent` 的构造参数 | `context_config` / `react_config` |
| `04-routes` | HTTP 路由 + CLI 命令 | `routes.add` / `commands.add` |
| `05-services` | 提供服务、按名称注入、惰性代理 | `ctx.provide` |
| `06-skills` | `SYSTEM.md` 与 `skills/` 由内置插件读 | 约定路径 |

这些示例都被 `tests/test_examples.py` 跑一遍 —— **文档里的示例最容易腐烂**,
抄的人不知道它已过期。

## 用 HTTP 模式看路由

```bash
cd examples/04-routes
sidecar --mode http --port 8000
curl --noproxy '*' http://127.0.0.1:8000/status
curl --noproxy '*' http://127.0.0.1:8000/info | python -m json.tool
```

## 看插件加载报告

```bash
cd examples/05-services
sidecar inspect
```

`inspect` 会列出每个插件的状态、贡献了什么、谁注册了哪个工具、有没有未解析的依赖。
