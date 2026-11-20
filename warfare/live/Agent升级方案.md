# live 桥接层升级为 Agent 的方案

> 现状：`live/` 的 AI 指挥是**单对话 LLM**——`ai_commander.py` 每回合调一次 `provider.chat(system, user)`，`providers.py` 只支持 `system + user` 两条消息，没有工具调用（function calling）。
>
> 问题：游戏规则、兵种数值、据点/地形这些信息，LLM 只能通过 `system_prompt` 里的「要点版」知道（还占每回合固定 token），写在 `.md` 文档里 LLM 永远不会主动读。
>
> 目标：把 LLM 升级成 **Agent**——给它工具（function calling），让它**按需查阅**规则、数值、据点，把「一次性喂全」变成「需要时再查」。

## 1. 核心思路：工具调用循环

把每回合的「一次 chat」换成「循环 chat」：

```
user 消息（记忆 + 战报）
   ↓
LLM 决策 ──返回 tool_call?──→ 是 → 执行工具 → 结果追加进对话 → 再问 LLM
   ↓ 否
最终文本（决策思路 + 指令）
   ↓
走现有 extract_plan / parse_llm / finish_turn 流程（不动）
```

每回合最多循环 `max_tool_rounds` 次（默认 3），防止 LLM 无限查。

## 2. 工具集（4 个，按价值排序）

| 工具 | 参数 | 返回 | 数据源 |
|---|---|---|---|
| `get_unit_stats` | `kind`（兵种英文名） | cost/hp/dmg/射程/速度/climb/组人数 + 特殊机制（架枪/冲锋/盾减伤/高地加成）+ 克制 | `init.defs` + 硬编码机制表 |
| `get_aas_rules` | 无 | AAS 完整规则：链序推进、占点（人数优势 + 10 秒）、首占 +400、胜负三条件 | 硬编码 `RULES` dict |
| `get_zones` | 无 | 所有据点：名字/本地坐标/`order` 链序/当前归属/占领进度 | `init.zones`（已解析，含 order） |
| `get_height` | `x,z` | 某点地形高度/可走性 | `init` 的 GRID（需保留） |

`get_height` 是可选增强（现在 `protocol.py` 把 GRID 丢弃、只留边界），第一版先不做，聚焦前三个。

**数据源的关键决策**：兵种数值**不硬编码**，直接从引擎 init 块的 `DEF` 行拿（`init.defs` 已解析）——单一真相源是引擎，不会漂移。只有引擎没在协议里发的「AAS 规则 + 兵种特殊机制 + 克制关系」才硬编码成一个 `RULES` dict（放 `tools.py`；不是 `.md`，`.md` 继续给人看）。

## 3. 改动文件

### ① `providers.py`（核心）

Provider 接口加一个方法，**工具循环封装在 provider 内部**（两套协议的消息格式差异不外露）：

```python
def run_agent(self, system, user, tools, execute_tool, max_rounds=3, budget=None):
    """工具调用循环，返回 (最终文本, 消耗的调用次数)。
    - tools：中间格式 [{"name","description","params"}]，provider 各自转成本协议的 tools 参数
    - execute_tool(name, args_dict) -> str：执行一个工具
    - budget：剩余可调用次数（None = 不限）
    默认实现：不调工具，直接单次对话（mock 走这里）。"""
```

三个 provider 分别实现：

- **OpenAI 兼容**（`OpenAICompatProvider`）：请求加 `tools` + `tool_choice:"auto"`；响应读 `message.tool_calls`；工具结果作为 `role:"tool"` 消息喂回。
- **Anthropic**（`AnthropicProvider`）：请求加 `tools`（`input_schema`）；响应读 `content` 里的 `tool_use` 块；工具结果作为 `role:"user"` + `tool_result` 块喂回。
- **mock**（`MockProvider`）：继承默认实现，直接返回文本，走单对话路径——全链路验证不受影响。

### ② `tools.py`（新文件）

- `TOOLS`：工具定义（中间格式 `[{"name","description","params"}]`，provider 各自转成本协议的 tools 参数）。
- `RULES`：硬编码的 AAS 规则 + 兵种特殊机制 + 克制关系（Python dict，带注释，与 `defs.hpp` 对齐）。
- `execute(name, args, init, tick) -> str`：执行工具，返回喂给 LLM 的**精炼**字符串。

### ③ `ai_commander.py`（决策循环）

把单次 `chat` 换成 `run_agent`：

```python
budget = max_calls - call_count
final, used = provider.run_agent(
    system, user, tools.TOOLS,
    lambda name, args: tools.execute(name, args, init, st),
    max_rounds=max_tool_rounds, budget=budget)
call_count += used
# final 走现有 extract_plan / parse_llm
```

记忆（`memory.render`）、计划（`extract_plan`）、指令解析（`parse_llm`）**全部不动**——工具循环只替换「怎么拿到 LLM 最终文本」这一段。

### ④ `README.md`

更新「max_calls_per_match」语义（见下），补一句「已支持工具调用」。

## 4. 成本控制

- `max_tool_rounds`（默认 3）：每回合工具循环轮数上限。
- `max_calls_per_match` 语义从「每回合一次、总封顶」变成「**总 API 调用次数**封顶」——工具循环里每一轮 LLM 调用都 +1。
- 工具 schema 不进 messages（不占每回合 token），但**工具结果**会进（下一轮 LLM 看得见），所以工具返回要精炼。

## 5. 验证

1. **mock 全链路**：mock 无工具调用，走单对话路径，现有流程照常跑通。
2. **工具单元测**：直接调 `tools.execute("get_unit_stats", {"kind":"archer"}, init, st)`，检查返回内容对。
3. **真实跑一场**：看日志里 LLM 是否「开局先查规则/据点、战斗时查兵种数值」，工具结果是否合理；对比不查规则的基线，看决策质量是否提升。

## 6. 权衡 / 风险

- **好处**：规则/数值「按需加载」，不占每回合固定 token；LLM 能查精确数值而非靠常识猜。
- **代价**：一次决策可能变多次 API 调用（慢 + 贵）；要同时维护 OpenAI/Anthropic 两套工具协议；`get_height` 若要做，得让 `protocol.py` 保留整张 GRID（大地图约 3.5MB/座位，可接受）。
- **风险**：LLM 可能「乱查」浪费轮数（靠 `max_tool_rounds` 兜底）；工具返回太长会重新制造信息淹没（所以返回要精炼）。

## 7. 待定参数（默认值，实现时可调）

| 参数 | 默认 | 说明 |
|---|---|---|
| 工具集 | 前 3 个 | `get_height` 地形查询二期再做 |
| 规则数据源 | `tools.py` 的 `RULES` dict | 备选：独立 `rules.json`（可读但两处维护） |
| `max_tool_rounds` | 3 | 每回合工具循环轮数上限 |
