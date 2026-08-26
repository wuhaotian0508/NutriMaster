# NutriMaster Pi Runtime

This directory is the NutriMaster agent-runtime boundary. It contains a
model-backed Pi chat runtime with a stable HTTP/SSE contract and the approved
`rag_search` and `experiment_design` tool adapters.

## Setup

```bash
nvm use
npm install
export OPENAI_API_KEY=...
export NUTRIMASTER_PI_MODEL=deepseek-v4-flash
npm start
```

The runtime requires Node `>=22.19.0`; `.nvmrc` pins the currently tested
version. `NUTRIMASTER_PI_MODEL` is the raw model ID used by the existing
OpenAI-compatible gateway, for example `gpt-4.1-mini` or `deepseek-v4-flash`.
The public model descriptor is `nutrimaster/<model-id>`. The runtime does not
expose Pi's file or shell tools; only the two NutriMaster tools are available.

## HTTP contract

`GET /healthz` reports whether the process is alive.

`POST /v1/chat/stream` accepts:

```json
{
  "messages": [{"role": "user", "content": "你好"}],
  "model": "openai/gpt-4.1-mini"
}
```

It returns SSE events using the pre-existing frontend event names and tool
events:

- `tools_enabled`: `{ "tools": ["rag_search", "experiment_design"] }`
- `tool_call`: `{ "tool": "rag_search", "args": { ... } }`
- `tool_result`: `{ "tool": "rag_search", "content": "..." }`
- `citations`: `{ "data": [ ... ] }`
- `graph_evidence`: `{ "data": [ ... ] }`
- `text`: `{ "data": "..." }`
- `error`: `{ "data": "..." }`
- `done`: `{}`

The request is stateless: callers send the conversation history in `messages`.
The runtime remains restricted to the two approved NutriMaster tools.

The runtime listens on `127.0.0.1:8787` by default. Keep it behind the main
NutriMaster API/reverse proxy so its unauthenticated internal endpoint is never
exposed directly to the public internet.

## 最小 Agent + 插件演示

这个离线示例不需要 API Key，用来直观看清 Agent 和插件的分工：

```bash
cd pi-runtime
npm run demo:plugin -- "请帮我算 12 + 30"
```

插件通过 `registerTool` 注册 `add`；Agent 解析输入、决定调用该工具，
再把插件返回的结果组织成回答。示例位于
`examples/simple-agent-plugin/`。
