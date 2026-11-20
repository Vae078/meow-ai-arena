# AI 实时对战（纸上谈兵）

让两个大模型（LLM）在对战中实时观察战场、实时下达指令——替代赛前编译好的 C++ 指挥程序。引擎 `war.exe` 不感知对面是 LLM 还是编译好的 bot，因为它和桥接程序之间仍是原来的 stdin/stdout 指挥协议。

设计文档见 [Agent升级方案.md](Agent升级方案.md)。

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

`--ai` 两个位置各是一个「指挥官」，可以是：

- AI 配置 `.json`（LLM 桥接，见下）
- 确定性程序 `.cpp`（自动编译成 exe 再用）或 `.exe`
- `-`（不用程序，按布阵姿态 + 默认行为打）

所以 AI 对确定性程序只需把一个位置换成 `.cpp` / `.exe`：

```bash
# 甲方 AI、乙方确定性程序（gpt-6.2-sol 的 commander.cpp）
python battle/live/run_live.py \
  --map 樱丘 \
  --deploy battle/bots/gpt-6.2-sol/duel.json battle/bots/gpt-6.2-sol/duel.json \
  --ai 甲.json battle/bots/gpt-6.2-sol/commander.cpp \
  -o battle/live/out
```

跑完输出：`out/result.json`（胜负）、`out/replays/last.wrp`（回放，可 `python battle/workspace/play.py replay` 复盘或 renderer 出片）、`out/logs/`（每边决策轨迹）。

注意：确定性程序每个物理 tick（0.1 秒）都能响应，AI 每 `decision_interval_secs` 秒才决策一次，二者节奏天然不同；想公平比较，可把 AI 的决策间隔调小（受 `max_calls_per_match` 和 API 速度限制）。

## AI 配置（每边一份 json）

字段：

| 字段 | 说明 | 默认 |
|---|---|---|
| `provider` | `mock` / `openai` / `anthropic` | `mock` |
| `decision_interval_secs` | 每几秒决策一次（两边应一致，保证公平） | `3` |
| `max_calls_per_match` | 每边封顶 LLM 调用次数（含工具调用，每轮工具循环算一次），到顶后空指令优雅降级 | `60` |
| `max_tool_rounds` | 每回合工具调用循环的轮数上限（LLM 可查规则/数值/据点） | `3` |
| `system_prompt` | 自定义 system prompt（空 = 用默认） | `""` |
| `model` | 模型名（openai/anthropic 需要） | — |
| `base_url` | OpenAI 兼容接口地址（openai 需要） | `https://api.openai.com/v1` |
| `api_key` | API key（直接写进配置；优先级高于 `api_key_env`） | — |
| `api_key_env` | API key 所在的环境变量名 | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` |
| `temperature` / `max_tokens` | 采样参数 | `0.7` / `1000` |
| `mock_reply` | mock 专用：固定回复文本（测试指令解析用） | `""` |
| `agent` | 指挥官 agent 记忆（对象，子字段见下） | `{}` |

`agent` 子字段（缺省即默认，都可省）：

| 字段 | 说明 | 默认 |
|---|---|---|
| `history_len` | 战况史保留最近几轮 | `3` |
| `memory_max_chars` | 记忆块最大字符数（超长截断） | `1500` |
| `plan_enabled` | 是否启用 LLM 自编辑「作战计划」 | `true` |

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

key 优先取配置里的 `api_key` 字段，否则从 `api_key_env` 环境变量读。含 key 的配置文件（`*.local.json`）已被 `.gitignore` 忽略，切勿提交。OpenAI 兼容用标准库 `urllib`（零依赖）；anthropic 需要 `pip install anthropic`。

## 说明

- 每场是同步阻塞：LLM 思考时游戏暂停（决策点之间快进）。回放按游戏时间记录，播放流畅，无停顿帧。
- 士兵保留引擎内置的默认行为（寻路/索敌/战斗），LLM 只下发高层指令（`move/attack/hold/free/aim/detonate/push`）。
- 指令逐行文本，见 `commands.py`。
- LLM 是 Agent：可调用工具（`get_unit_stats`/`get_aas_rules`/`get_zones`）按需查兵种数值、AAS 规则、据点，见 `tools.py` 和 `Agent升级方案.md`。
