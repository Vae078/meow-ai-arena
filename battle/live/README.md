# AI 实时对战（纸上谈兵）

让两个大模型（LLM）在对战中实时观察战场、实时下达指令——替代赛前编译好的 C++ 指挥程序。引擎 `war.exe` 不感知对面是 LLM 还是编译好的 bot，因为它和桥接程序之间仍是原来的 stdin/stdout 指挥协议。

设计文档见 [PLAN.md](PLAN.md)。

## 前置

1. 编译好 `battle/workspace/war.exe`（`python battle/build_engine.py`）。
2. 两份布阵 json（可复用 `battle/bots/*/duel.json`），两份 AI 配置 json。

## 用法

```bash
python battle/live/run_live.py \
  --map 樱丘 \
  --deploy battle/bots/gpt-6.2-sol/duel.json battle/bots/claude-fable-5.2/duel.json \
  --ai 甲.json 乙.json \
  -o battle/live/out
```

跑完输出：`out/result.json`（胜负）、`out/replays/last.wrp`（回放，可 `python battle/workspace/play.py replay` 复盘或 renderer 出片）、`out/logs/`（每边决策轨迹）。

## AI 配置（每边一份 json）

字段：

| 字段 | 说明 | 默认 |
|---|---|---|
| `provider` | `mock` / `openai` / `anthropic` | `mock` |
| `decision_interval_secs` | 每几秒决策一次（两边应一致，保证公平） | `3` |
| `max_calls_per_match` | 每边封顶 LLM 调用次数，到顶后空指令优雅降级 | `60` |
| `system_prompt` | 自定义 system prompt（空 = 用默认） | `""` |
| `model` | 模型名（openai/anthropic 需要） | — |
| `base_url` | OpenAI 兼容接口地址（openai 需要） | `https://api.openai.com/v1` |
| `api_key_env` | API key 所在的环境变量名 | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` |
| `temperature` / `max_tokens` | 采样参数 | `0.7` / `1000` |
| `mock_reply` | mock 专用：固定回复文本（测试指令解析用） | `""` |

### 示例

**mock（不花 API 钱，验证全链路）**：

```json
{ "provider": "mock", "mock_reply": "", "decision_interval_secs": 3.0, "max_calls_per_match": 60 }
```

**OpenAI 兼容（DeepSeek 为例）**：

```json
{
  "provider": "openai",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com",
  "api_key_env": "DEEPSEEK_API_KEY",
  "decision_interval_secs": 3.0,
  "max_calls_per_match": 60,
  "temperature": 0.7,
  "max_tokens": 1000
}
```

**Claude（Anthropic）**：

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "api_key_env": "ANTHROPIC_API_KEY",
  "decision_interval_secs": 3.0,
  "max_calls_per_match": 60
}
```

key 从环境变量读，不落盘。OpenAI 兼容用标准库 `urllib`（零依赖）；anthropic 需要 `pip install anthropic`。

## 说明

- 每场是同步阻塞：LLM 思考时游戏暂停（决策点之间快进）。回放按游戏时间记录，播放流畅，无停顿帧。
- 士兵保留引擎内置的默认行为（寻路/索敌/战斗），LLM 只下发高层指令（`move/attack/hold/free/aim/detonate/push`）。
- 指令逐行文本，见 `commands.py`。
