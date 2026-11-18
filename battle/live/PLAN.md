# 纸上谈兵：AI 实时控制对战（技术方案）

> 状态：技术评审稿（未实施）。本文档把方案精确到协议字节和函数级细节。

## 1. 一句话方案

不改引擎一行代码，利用现有的「指挥程序（commander）子进程」边界：写一个 **Python 桥接程序**，替代赛前编译好的 C++ bot。引擎照常每 0.1s 把战场状态发到这个进程，桥接程序把状态**压成几百字的战报**喂给 LLM，把 LLM 回的指令翻译成引擎认识的文本发回去。

## 2. 背景与目标

当前 `battle/` 的玩法是「AI 赛前写好指挥程序（commander.cpp）→ 编译成 exe → 引擎离线跑完出结果」。目标改成**「两边接入 AI 实时控制」**：对战中不再是预先编译好的 bot，而是让大模型（LLM）在比赛进行时实时观察战场、实时下达指令。

本期范围（已确认）：

- **观看方式**：先只要文本日志（打印 AI 每步决策 + 战况），不做实时画面/查看器。
- **AI 感知**：纯文本摘要（把战场状态压成文字喂给 LLM），不发图。
- **模型**：还没定，做成可配置（抽象出 provider 接口，模型名 / API key / base_url 走配置）。
- **模式**：先做 1v1 对战（`duel`，两个 AI 座位）。melee、level 作为后续扩展。

## 3. 角色定位：指挥官（外部 AI）与士兵（内部 AI）

这是整个设计最根本的定位，必须一开始就明确：

- **外部 AI = 指挥官**。它不是"实时操控每一个士兵的手"，而是根据战场态势**偶尔下发战略指令**。
- **每个士兵保留引擎内置的"内部 AI"**：寻路（0.5m 格子 A*）、索敌（就近 + 定期重评估）、战斗（各兵种自己的攻击逻辑：弓手射程、骑兵冲锋、枪兵架枪、炮仗兵自爆……）。
- **AI 的指令是"高层覆盖"，可选、稀疏**。一条指令下发后一直有效直到下一条；不发指令时，士兵用默认行为自动打满全场，不会傻站。

因此决策频率可以很慢（下面定为 3s），AI 的价值在**战略层**：占高地、集火、卡位、撤退、触发特殊机制（炸桥/推雪球）。LLM 不需要、也不应该逐帧微操。

## 4. 核心思路：复用「指挥程序」边界

引擎 `war.exe` 每 0.1 秒（一回合）把战场状态打成文本，发给独立的 **commander 子进程**，读回一行指令。现在的 commander 是赛前编译的 C++ 程序；要「实时控制」，就是把 commander 换成桥接程序。

关键源码依据：

- 驱动循环：`battle/engine/src/host.cpp:587 run_match` —— `decision_step()` → 发 `tick_text()` → 读一行回复 → `apply_orders()` → `do_step()`。
- 协议：`battle/workspace/commander/commander.hpp` —— `read_init()`（到 `END`）/ `read_tick()`（到 `E`）/ 指令编码 `pt()/op()`。
- 时限：`battle/engine/src/host.hpp:16 Limits` —— 每回合 2s 墙钟、累计 50 次超时杀进程。
- **时限可用命令行放宽，无需改引擎**：`battle/engine/src/main.cpp:72 common_opt` 已支持 `--wall-ms / --first-wall-ms / --max-timeouts / --match-wall-s`。

## 5. 进程拓扑与数据流

```
                    ┌─────────────────────────────────────────┐
                    │  war.exe（引擎，不改）                    │
                    │   host.cpp run_match() 驱动循环          │
                    └───────┬──────────────────┬──────────────┘
              stdin/stdout │                  │ stdin/stdout
                (文本协议)  │                  │  (文本协议)
              ┌────────────▼───┐          ┌───▼────────────┐
              │ ai_commander.py │          │ ai_commander.py │   ← 新写的桥接程序
              │ (座位0，甲方)    │          │ (座位1，乙方)     │
              └───────┬────────┘          └───┬────────────┘
                      │  HTTPS                │  HTTPS
              ┌───────▼────────┐      ┌───────▼────────┐
              │   LLM（可配）   │      │   LLM（可配）    │  ← provider 抽象，两边可不同
              └────────────────┘      └────────────────┘
```

要点：

- 引擎和桥接程序之间是 **stdin/stdout 逐行文本**，和 C++ bot 完全同一种协议——引擎**感知不到**对面是大模型还是编译好的程序。
- 两边桥接是**两个独立子进程**，各自的 LLM 调用天然并行。
- 桥接程序到 LLM 是 HTTPS 调用，走 `providers.py` 抽象，模型可换。

## 6. 两个核心问题及解法

### 6.1 LLM 延迟（1~10s）vs 引擎 tick（0.1s）

**解法：让引擎在决策点"等"LLM，中间快进（同步阻塞，作为初步实现）。**

引擎驱动循环（`host.cpp:615`）：

```cpp
while (!w.over()) {
    if (w.decision_step()) {          // 每 6 步 = 每 0.1s 一个回合
        obs = w.tick_text();          // 生成状态文本
        for each commander: ask(obs)  // 写进子进程 stdin
        for each commander: poll()    // 阻塞等一行回复
        for each: apply_orders(line)  // 应用指令
    }
    w.do_step();                      // 推 1/60 秒物理
}
```

`poll()` 是**同步阻塞**的。所以：

- **决策回合**（桥接程序调 LLM 的那 0.1s）：引擎被 `poll()` 卡住，等于游戏暂停，等 LLM 想完。
- **非决策回合**：桥接程序**立即**回一行空指令，引擎几乎零延迟滑过去。

**为什么选阻塞而不是异步？** 异步（游戏继续跑、LLM 事后追指令）会让指令天然过期，LLM 还得做预测。阻塞模型下，**LLM 看到的战场 = 指令生效那一刻的战场，零延迟、零预测**。代价是游戏暂停——对"文本日志"这一期完全可接受。

**⚠️ 未来约束**：一旦要上**实时渲染**，这个阻塞模型就不能按"实时"渲染了——游戏会卡在等 LLM。届时按**回合制游戏**展开：每个决策点渲染一帧、AI 思考、再推进（不是改成异步）。方向已确认，本期不做。

**决策频率定为 3s**（非 0.1s，也非 2s）：0.1s 太密集、没意义（士兵有内部 AI 兜底）；3s 一次战略决策足够。整场墙钟时间 ≈ **决策次数 × LLM 单次延迟**。90 秒的局、每 3s 决策一次 = 30 次决策/边 × ~3s ≈ **1.5 分钟一场**。

**时限放宽**（全用现成参数，不改引擎）：

- `--wall-ms 180000 --first-wall-ms 180000`：每回合 2s 墙钟上限放宽到 3 分钟，容纳慢 LLM。
- `--max-timeouts 0`：关掉"累计 CPU 超时 50 次杀进程"。
- `--match-wall-s 0`：关掉整场 120s 的看门狗。

### 6.2 双方决策回合公平

要求：**两边决策回合时长相等**。这一点在现有架构下天然成立，但要在实现里显式保证：

- 引擎在**同一个 `decision_step`** 同时向两边发 tick（`host.cpp:621` 先对所有 `ask`，再统一 `poll`），两边读到的是**同一条 tick 流**。
- 两边桥接都用 `tick % (interval*10) == 0` 判定，`interval` 相同 → 两边在**相同的游戏时间点**决策。
- 引擎**等两边都回复**才推进（`host.cpp:626`），所以慢的一方不会让对方"多出游戏时间"来思考——游戏只是整体等待。

实现上：`run_live.py` 用**同一个 `decision_interval_secs` 作用于两边**（全局一份，或强制两边相等），从根上保证公平。

### 6.3 状态 100~300KB 塞不进 context

**解法：桥接程序只把"战报"发给 LLM，原始数据 LLM 永远看不到。**

100~300KB 里 ~95% 是 `GRID` 高度图（~2 万格子的数字），那是给 C++ bot 的 A* 寻路用的。**LLM 不做寻路**（士兵的默认行为自己 A* 寻路），它只需要"战场多大、哪高哪低、泥塘/计分区在哪"一句话。桥接程序在 `protocol.py` 解析时**直接丢弃高度图**，`summary.py` 只产出一小段文字。

### 6.4 回放流畅性：阻塞暂停不进回放

**复用现有回放机制（`--replay out.wrp`），回放天然流畅，无需额外处理。**

原因：回放帧是**按游戏步号（step）记录的**（`sim.cpp doStep()` 里 `stepN % rec_every == 0` 时 `writeFrame()`），每 2 步一帧、按 30fps 游戏时间播放；回放格式（`回放格式.md`）里**没有墙钟时间戳**，只有 `step` + `dt`。

而 LLM 计算的阻塞暂停发生在**两次 `do_step()` 之间**（引擎卡在 `poll()` 等回复），这段时间游戏步号不前进、也就**不产生任何帧**。

所以录制时 LLM 的 3s 思考只让录制墙钟变长，回放里对应位置**没有停顿帧**，播放出来仍是 90 秒游戏时间的流畅画面。M1 验证时确认"回放流畅、无停顿帧"即可。

## 7. 通信协议（精确到字段）

### 7.1 引擎 → AI：开局一次 init 块

```
WAR 1
MODE duel
MAP 樱丘
SEAT 0 TEAM 0 TEAMS 2 SEATS 2
FRAME <ox> <oz> <yaw>            ← 本地/世界坐标换算的基准
LIMIT <总步数> <6> <60>
WATER none|static <h>|tide <t0> <t1> <h0> <h1>
KILLY <y>
DEF 0 militia 10 45 9 1.2 0.3 1.0 3.4 0.30 1.1   ← ×11 个兵种
RULE <弓手射程> <高度加成> <射程上限> <投石min> <投石max> <爆炸半径>
GRID <w> <h> <x0> <z0> <cell>
  <h 行 × w 个高度值，'x'=空洞>        ← 大块，桥接程序丢弃
Z <名> circle <cx> <cz> <r> / rect <x0> <z0> <x1> <z1>
SCORE <计分区名 | ->
UNITS <n>
  <id> <kind> <team> <seat> <tag>
PROPS <n>
  <id> <type> <team> <x> <y> <z> <tag>
END
```

### 7.2 引擎 → AI：每 0.1s 一次 tick 块

```
T <tick> <step> <水面cm，-99999=无水>
U <n>
  <id> <state> <x> <y> <z> <vx> <vz> <hp×10> <cd|起手> <flags> <朝向×1000> <order> <target>
V <n>
  <事件文本，如 "terrain_removed 桥中段" / "boss 41 charge">
B <n>                          ← 镇山兽（仅第5关）
P <n>                          ← 道具：军旗/火药桶/雪球/木栅/立石
S <n>                          ← 飞行中的石头、滚动中的雪球
E
```

单位字段详解：`state` 0站/1倒/2起身/3死；`x y z` 是**世界坐标厘米**；`flags` 位掩码含"架枪/点火/冲锋/腾空"；`hp×10` 表示血量；`cd|起手` 负数=正在起手、正数=冷却剩余步数。

### 7.3 AI → 引擎：每回合一行

```
<tick> [M <id> <世界x厘米> <世界z厘米>] [A <id> <目标id>] [H <id>] ...
```

指令编码表（`commander.hpp pt()/op()`）：

| LLM 说 | 编码 | 含义 |
|---|---|---|
| `move id x z` | `M id wx wz` | 走到某点（A* 绕路，路上不还手） |
| `attack id t` | `A id t` | 追打目标（单位或道具） |
| `hold id` | `H id` | 原地守 |
| `free id` | `F id` | 回默认行为 |
| `aim id x z` | `P id wx wz` | 弓/投石车朝地点盲射 |
| `detonate id` | `D id` | 炮仗兵点火 |
| `push id t` | `U id t` | 推雪球 |

**坐标换算**（桥接程序必须精确复刻 `commander.hpp:182`）：

```
c=cos(yaw), s=sin(yaw)     （90°倍数时 snap 成精确 0/±1）
to_world:  wx = ox + x*c + z*s ;  wz = oz - x*s + z*c
to_local:  dx = wx-ox ; dz = wz-oz ; x = dx*c - dz*s ; z = dx*s + dz*c
```

**关键约定**：桥上收到的状态是**世界坐标**，桥把它转成**本地坐标**（x 向右、z 朝敌人）给 LLM 看；LLM 回的 `move/aim` 坐标是本地，桥再转回**世界厘米**发出去。

## 8. 一次决策的完整时序

以「第 3 秒该决策了」为例（3s = 30 回合）：

```
引擎： tick 块(第30回合) ──写入──▶ 桥接 stdin
桥接： 读 tick 块 → 解析 → tick%30==0 判定是决策回合
        │
        ├─ 构造 summary（几百字战报）
        ├─ provider.chat(system, summary)   ← 阻塞 1~10s，引擎此刻在 poll() 等
        ├─ 拿到 LLM 回复 → commands.parse() → 校验 → 本地坐标转世界
        └─ 写 stdout: "30 M 12 530 -1200 A 3 25 H 7\n"
引擎： poll() 拿到这行 → apply_orders() 应用 → do_step() 继续
        │
        └─ 接下来 3 秒（29 个非决策回合），桥每回合立即回 "31\n" "32\n" ...（空=保持上一条）
```

关键点：**空行 ≠ 无指令**，而是"保持上一条指令"（引擎指令语义是"下了就一直有效，直到下一条"）。桥只需要在决策回合回指令，其余回合回空行。

## 9. 各模块设计（函数级）

新增文件（全部在 `battle/live/` 下）：

```
battle/live/
  PLAN.md               本文档
  README.md             用法说明
  config.example.json   示例配置
  run_live.py           入口
  ai_commander.py       桥接程序本体
  protocol.py           协议解析
  summary.py            状态 → 战报
  commands.py           LLM 回复 → 指令
  providers.py          LLM provider 抽象
```

### 9.1 `protocol.py` —— 协议解析（移植 commander.hpp）

```python
class InitState:  # mode, map, seat, team, teams, ox, oz, yaw, defs{}, rules,
                  # zones[], score_zone, water, units{id:(kind,team,tag)}, props{...}
    def to_world(self, x, z) -> (wx, wz): ...
    def to_local(self, wx, wz) -> (x, z): ...

class TickState:  # tick, time, water, units[], events[], bosses[], props[], flying[]
def parse_init(reader) -> InitState       # 读行直到 "END"
def parse_tick(reader, init) -> TickState # 读行直到 "E"
def encode_line(tick, cmds) -> str        # 拼 "<tick> M ... A ..."
```

直接把 `commander.hpp` 的 `read_init()/read_tick()` 逻辑翻译成 Python，逐行 `rfind` 前缀解析。**丢弃 GRID**（或只存边界 `x0/z0/cell` 供坐标校验）。

### 9.2 `summary.py` —— 状态 → 战报

把 `TickState` 压成（约 500~1500 token）：

```
【第 45.0 秒 · 1v1 · 樱丘 · 时限 90 秒】
目标：全歼对方；时间到比山头上兵总造价。计分区「山头」。
我方 8 单位（造价 620）：
  #12 长枪兵 (5.3,-12.0) 血80 站 架枪
  #3  弓手   (-2.0,-8.5) 血60 站 冷却0.2
敌方 7 单位：
  #25 骑兵   (4.0,15.0) 血160 冲锋
道具：军旗#40(敌)(0,20) 血300
水面：无
近况：#3 射中 #25 16点；#18 阵亡
```

只列**活着的**单位，坐标是本地，兵种名用中文，状态用短标签。system prompt 写规则 + 指令语法 + 策略建议。

### 9.3 `commands.py` —— LLM 回复 → 指令

LLM 输出**逐行文本**（不是 JSON——对任意模型更稳、省 token）：

```
attack 12 34
move 3 5.0 -10.0
hold 7
free 9
```

```python
def parse(llm_text, tick, init) -> (cmds, errors):
    # 每行 split 成 (op, id, ...)；校验：id 是我方且活着、target 存在、坐标在界内
    # move/aim 的本地坐标 → to_world → ×100 取整
    # 非法行 → errors（记日志，不中断，不生成指令）
```

### 9.4 `providers.py` —— 模型可配置

```python
class Provider:
    def chat(self, system: str, user: str) -> str: ...
def make_provider(cfg) -> Provider   # cfg["provider"] 分发
```

- `MockProvider`：不发网络，返回脚本化/固定指令——**M1 全链路验证用，不花 API 钱**。
- `OpenAICompatProvider`：**零依赖**，标准库 `urllib` POST `{base_url}/chat/completions`，`base_url` 可配 → 覆盖 OpenAI/DeepSeek/Qwen/GLM/Moonshot/Ollama。
- `AnthropicProvider`：`anthropic` SDK（走 claude-api skill 的模型选型 + prompt caching）。

配置项：`provider / model / api_key_env / base_url / temperature / max_tokens / system_prompt / decision_interval_secs / max_calls_per_match`（默认 60）。key 从环境变量读，不落盘。

### 9.5 `ai_commander.py` —— 桥接程序本体

```
1. stdin/stdout 设 UTF-8 + 无缓冲（-u）
2. 读 init 块 → InitState → 构造 system prompt
3. 循环读 tick 块 → TickState
4. 若 tick % (interval*10) == 0：summary + provider.chat + parse → 得到本回合指令
5. 写 stdout 一行 "<tick> [指令]"
6. 日志全走 stderr（引擎 --stderr-dir 会落盘）
```

**三条铁律**：只往 stdout 写协议行；日志走 stderr；每回合必须及时回一行。

### 9.6 `run_live.py` —— 入口

拼命令并 `subprocess.run`：

```
war.exe run maps/樱丘.json
  --deploy 甲.json --deploy 乙.json
  --cmd "cmd:<python.exe> -u ai_commander.py --config 甲.json"
  --cmd "cmd:<python.exe> -u ai_commander.py --config 乙.json"
  --wall-ms 180000 --first-wall-ms 180000 --max-timeouts 0 --match-wall-s 0
  --replay out.wrp --json result.json --stderr-dir logs/
```

- **公平约束**：两边用**同一个 `decision_interval_secs`**（全局一份），保证决策回合时长相等。
- 每个座位可独立：AI 桥接（`--config`）或普通编译好的 bot exe（方便"AI vs 现有 bot"）。

## 10. 关键权衡

| 决策 | 选择 | 代价 | 为什么不选另一个 |
|---|---|---|---|
| 阻塞 vs 异步 | 阻塞（M1） | 游戏在决策点暂停 | 异步指令过期、要预测 |
| 决策频率 | 每 3s | 反应慢半拍 | 士兵有内部 AI 兜底，密集决策无意义 |
| 状态形式 | 纯文本 | 丢空间信息 | 图像慢且贵，本期先不做 |
| 指令形式 | 逐行文本 | 解析要自己写 | JSON 对弱模型不稳 |
| 坐标 | 本地坐标给 LLM | 要换算 | 世界坐标不直观 |

## 11. 风险与边界情况

1. **LLM 超时** → 引擎杀桥接进程（ST_LOST），之后单位永远保持最后一条指令。缓解：`wall-ms` 设 3 分钟；桥接程序对单次调用做超时兜底（超时回空指令，不硬等）。
2. **LLM 回垃圾** → `commands.parse` 全部忽略，单位保持上一条，桥不崩。这是硬要求。
3. **LLM 回不存在的 id** → 校验丢弃。
4. **非确定性** → LLM 天然非确定，同一局打两次不一样。**接受**：这是"演示/对战"模式，不是"复现"模式（复现仍走原赛道）。
5. **成本/延迟** → 90s 局 ≈ 30 次决策/边 ≈ 60 次 API 调用/场。
6. **`cmd:` 启动机制** → host 的 `cmd:` 前缀当命令行跑（标注"测试用"），要验证带中文路径/空格能不能起。缓解：python.exe 用绝对路径、引号包好；不行就写个 2 行 `.bat` 壳。
7. **编码** → 不设 UTF-8+无缓冲，中文会乱、行缓冲会卡。
8. **stderr 写太多** → 引擎对 stderr 有 1MB 截断（`STDERR_CAP`），日志要克制。

## 12. 分阶段交付

- **M1**：`protocol.py` + `commands.py` + `summary.py` + `providers.py`（含 Mock）+ `ai_commander.py` + `run_live.py`，跑通 **mock AI vs mock AI**，验证全链路。
- **M2**：接 `OpenAICompatProvider` 真跑，再按需接 `AnthropicProvider`。
- **M3**：README、config.example、摘要调优、重试。

## 13. 验证方案

- **解析正确性**：mock 对战日志里的单位数/坐标，与 `play.py replay` 复盘一致。
- **全链路**：`run_live.py` 一场，拿到 `result.json`（有 winner）+ `out.wrp`（可回放）+ `logs/*.txt`（有决策轨迹）。
- **健壮性**：故意喂非法指令/让 LLM 超时，确认桥不崩、空指令能继续打。

## 14. 范围外与未来扩展

本期不做：

- 实时画面/查看器（用户选了先只要文本日志）。
- 图像输入（多模态）感知。
- melee（4 座）、level（AI vs 守军）模式。
- AI 实时设计布阵（deploy 仍是静态 json 文件）。
- 确定性/复现（LLM 天然非确定，此玩法不需要）。

未来扩展（已识别、待决策）：

- **实时渲染（回合制）**：当前同步阻塞模型下，未来实时渲染按**回合制游戏**展开——每个决策点渲染一帧、AI 思考、再推进（已确认方向，非异步）。本期不碰。
- **小队级指令**：当前指令粒度是"对单个兵下令"。若要更省 token，可定义"小队"抽象（如"所有弓手"），让 LLM 对小队下令。本期先不做。

## 15. 已确认

**已确认**：

1. 外部 AI = 指挥官（下发战略指令）；士兵保留内部 AI（寻路/战斗/索敌）。
2. 决策频率 = **3s**（不是 0.1s 每回合微操）。
3. 同步阻塞作为 M1 初步实现，认可。
4. 双方决策回合时长相等（用同一个 `decision_interval_secs` 保证）。
5. 回放复用现有机制，阻塞暂停不进回放，回放保持流畅。
6. 未来实时渲染按回合制游戏展开（非异步）。

7. 每场每边**封顶 60 次 LLM 调用**（`max_calls_per_match`，可配）：满时长 90s ÷ 3s ≈ 30 次决策，60 = 2 倍留重试/安全阀；到顶后不再调 LLM，回空指令优雅降级（士兵按最后指令 + 默认行为打完，不崩）。
8. 不做"小队级"抽象，指令保持"逐行对单个兵下令"。
9. 每个决策点**最多重试 1 次**（网络错误/超时重试一次，再失败跳过该决策、回空指令）。
