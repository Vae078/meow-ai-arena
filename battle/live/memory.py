# -*- coding: utf-8 -*-
"""指挥官 agent 的记忆：跨轮持久，把无状态单次对话变成有记忆的连续决策。

两个存储，一个由代码管、一个由 LLM 管：
- Episodic memory（战况史）：harness 机械维护——上一轮指令 + 结果 diff + 最近 N 轮环形缓冲。
- Strategic memory（作战计划）：LLM 自编辑，通过回复里的 [计划]…[/计划] 段覆盖。
"""
from collections import Counter

_PLAN_OPEN = ("[计划]", "[PLAN]", "[plan]", "【计划】")
_PLAN_CLOSE = ("[/计划]", "[/PLAN]", "[/plan]", "【/计划】")


def extract_plan(text):
    """从 LLM 回复里提取 [计划]…[/计划] 段，返回 (plan_or_None, 剩余文本)。

    中英文方括号/全角括号都认；找不到闭合标记时取到文本末尾；
    没有计划段则返回 (None, 原文)。
    """
    if not text:
        return None, text
    open_idx, open_tag = -1, ""
    for tag in _PLAN_OPEN:
        i = text.find(tag)
        if i != -1 and (open_idx == -1 or i < open_idx):
            open_idx, open_tag = i, tag
    if open_idx == -1:
        return None, text
    start = open_idx + len(open_tag)
    close_idx, close_tag = -1, ""
    for tag in _PLAN_CLOSE:
        i = text.find(tag, start)
        if i != -1 and (close_idx == -1 or i < close_idx):
            close_idx, close_tag = i, tag
    if close_idx == -1:
        plan = text[start:].strip()
        rest = text[:open_idx].strip()
    else:
        plan = text[start:close_idx].strip()
        rest = (text[:open_idx] + "\n" + text[close_idx + len(close_tag):]).strip()
    return (plan if plan else None), rest


def summarize_tokens(tokens):
    """把协议 token 列表压成一句简写（按操作符计数），供战况史引用。"""
    if not tokens:
        return "无指令"
    c = Counter(t.split()[0] for t in tokens)
    return "、".join(f"{op}×{n}" for op, n in sorted(c.items()))


class MemoryStore:
    def __init__(self, agent_cfg=None):
        cfg = agent_cfg or {}
        self.history_len = max(0, int(cfg.get("history_len", 3)))
        self.memory_max_chars = int(cfg.get("memory_max_chars", 1500))
        self.plan_enabled = bool(cfg.get("plan_enabled", True))
        self.plan = ""
        self.last_tick = None
        self.last_orders = ""
        self.last_snapshot = None   # (mine_ids:set, enemy_ids:set)
        self.history = []           # list[str]，最近 history_len 条战况

    def _alive_sets(self, st):
        mine = {u.id for u in st.units if u.alive and u.mine}
        enemy = {u.id for u in st.units if u.alive and not u.mine}
        return mine, enemy

    def begin_turn(self, st):
        """决策前调用：用当前状态与上次快照算「上一轮结果」，追加进 history。

        首轮（无快照）直接返回；之后 diff 出我方/敌方伤亡，拼成一条战况。
        """
        if self.last_snapshot is None:
            return
        mine, enemy = self._alive_sets(st)
        prev_mine, prev_enemy = self.last_snapshot
        result = f"我方{len(prev_mine)}→{len(mine)}，敌方{len(prev_enemy)}→{len(enemy)}"
        mine_lost = prev_mine - mine
        enemy_lost = prev_enemy - enemy
        if mine_lost:
            result += "，我方阵亡#" + ",".join(map(str, sorted(mine_lost)))
        if enemy_lost:
            result += "，敌方阵亡#" + ",".join(map(str, sorted(enemy_lost)))
        self.history.append(f"回合{self.last_tick}：我下令 {self.last_orders}；结果 {result}")
        if len(self.history) > self.history_len:
            self.history = self.history[-self.history_len:]

    def finish_turn(self, st, tick, tokens, plan=None):
        """决策后调用：更新计划、上一轮指令、快照，供下一轮 begin_turn 用。"""
        if self.plan_enabled and plan:
            self.plan = plan
        self.last_tick = tick
        self.last_orders = summarize_tokens(tokens)
        self.last_snapshot = self._alive_sets(st)

    def render(self):
        """生成注入 prompt 的记忆块。"""
        lines = []
        if self.plan_enabled:
            lines.append("【作战计划】")
            lines.append(self.plan if self.plan else "(首轮：请根据开局布阵制定作战计划，之后可随时更新)")
            lines.append("")
        lines.append("【战况史】")
        if self.history:
            for h in self.history:
                lines.append("- " + h)
        else:
            lines.append("(首轮，暂无战况)")
        block = "\n".join(lines)
        if self.memory_max_chars > 0 and len(block) > self.memory_max_chars:
            block = block[:self.memory_max_chars].rstrip() + "\n…(记忆过长已截断)"
        return block
