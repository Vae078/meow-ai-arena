# -*- coding: utf-8 -*-
"""LLM 的工具（function calling）：把规则、兵种数值、据点按需喂给模型。

数据源：
- 兵种数值：引擎 init 块的 DEF 行（protocol.init.defs），单一真相源，不硬编码。
- 据点：init 块的 Z 行（protocol.init.zones，含 AAS 的 order 链序）+ 每回合 tick 的 caps。
- AAS 规则 / 兵种特殊机制 / 克制关系：引擎协议没发，硬编码在下面的 RULES（与 defs.hpp 对齐）。
"""

import protocol as P

# ---- 引擎协议没发、需要硬编码的部分（与 engine/src/defs.hpp 对齐）----

SQUAD_SIZE = {
    "militia": 12, "shield": 8, "spear": 8, "archer": 12,
    "cavalry": 6, "hammer": 6, "bomber": 6, "catapult": 2, "giant": 1,
}

# 每个兵种的定性机制 + 协议里没有的关键数值
MECHANICS = {
    "militia": "最便宜的人海炮灰，纯肉盾",
    "shield": "盾卫：正面左右 60° 内受伤 ×0.3、击退 ×0.35，正面扛伤害的肉盾",
    "spear": "长枪兵：站定 0.5 秒架枪，迎击速度 >3.5m/s 冲来的敌人（范围 2.6m、正面 40°），专克骑兵冲锋",
    "archer": "弓手：远程射程 28 米，站得越高射得越远（每高 1 米 +1.5 米，上限 36 米），被近身改用匕首（弱）",
    "cavalry": "骑兵：速度最快 7.5m/s，速度 >3.5 算冲锋，冲锋伤害 =5×速度，冲量很大，专克弓手",
    "hammer": "大锤：范围近战，一下最多打 3 人，重击退",
    "bomber": "炮仗兵：自爆，点火 0.6 秒后爆炸，中心 70 / 边缘 20 伤害，半径 3.5 米",
    "catapult": "投石车：攻城远程，射程 15~45 米，抛射石头砸「最密的一团」（3 米半径数人），不能移动",
    "giant": "大块头：血量 2000 的巨型单位，一下打 8 人，能翻 1.6 米台阶",
    "boss": "镇山兽：关卡 Boss，周期性冲撞，撞石墙会晕",
    "lord": "主帅：血量低，只听移动/原地守，要保护好",
}

COUNTERS = (
    "克制关系（非传递环）：长枪兵克骑兵（架枪迎击）、骑兵克弓手（冲进后排）、弓手克长枪兵（远程风筝）。"
    "定位环：近战 < 远程 < 攻城 < 掩体。"
)

AAS_RULES = {
    "链序推进": "据点排成推进链，只能占与自己已占点相邻的点；我方从链头（order 0）推、敌方从链尾推，不能跳点绕后",
    "占点": "据点区域内活兵数 > 敌方时进度前进，持续 10 秒才易主（一回合 8 秒，所以要跨回合守住）",
    "首占奖励": "首次占下一个自己没占过的点 +400 ticket（每点双方各一次），反复易主不再给",
    "买组": "花 ticket 在基地买一整组，组满编出生、走到前线（时间成本）",
    "胜利": "1) 占满全部据点立即胜；2) 敌方 ticket 花光且场上无活兵立即胜；3) 时间到比占点多 → 比剩余 ticket → 平局",
}

# ---- 工具定义（中间格式，provider 各自转成本协议的 tools 参数）----

TOOLS = [
    {
        "name": "get_unit_stats",
        "description": "查一个兵种的详细数值（造价/血量/伤害/射程/速度/翻越/组人数）和特殊机制、克制关系。参数 kind 用兵种英文名。",
        "params": {"kind": {"type": "string", "description": "兵种英文名：militia/shield/spear/archer/cavalry/hammer/bomber/catapult/giant/boss/lord"}},
    },
    {
        "name": "get_aas_rules",
        "description": "查 AAS 顺序占点的完整规则：链序推进、占点条件、首占奖励、买组、胜利条件。",
        "params": {},
    },
    {
        "name": "get_zones",
        "description": "查当前所有据点的名字、本地坐标、链序（order）、当前归属和占领进度。",
        "params": {},
    },
]


def execute(name, args, init, tick=None):
    """执行工具，返回喂给 LLM 的字符串。name 是工具名，args 是参数字典。"""
    if name == "get_unit_stats":
        return _unit_stats((args or {}).get("kind", ""), init)
    if name == "get_aas_rules":
        return _aas_rules()
    if name == "get_zones":
        return _zones(init, tick)
    return f"未知工具 {name}"


def _unit_stats(kind, init):
    if kind not in P.KIND_EN:
        return f"不认识的兵种「{kind}」，可选：{'/'.join(P.KIND_EN)}"
    i = P.KIND_EN.index(kind)
    d = init.defs.get(i)
    if d is None:
        return f"没有 {kind} 的数值"
    zh = P.KIND_ZH[i]
    n = SQUAD_SIZE.get(kind, 0)
    squad = f"每组 {n} 人" if n > 0 else "不可买"
    lines = [
        f"{kind}（{zh}）· {MECHANICS.get(kind, '')}",
        f"- 造价 {d.cost:g}/兵，{squad}（组价 {d.cost * n:g}）" if n > 0 else f"- 造价 {d.cost:g}/兵，不可买",
        f"- 血量 {d.hp:g}，伤害 {d.dmg:g}，出手一轮 {d.cycle:g} 秒",
        f"- 射程/够得着 {d.reach:g} 米，速度 {d.speed:g} m/s，翻越 {d.climb:g} 米",
        f"- 身体半径 {d.radius:g} 米",
    ]
    return "\n".join(lines) + "\n" + COUNTERS


def _aas_rules():
    lines = ["AAS 顺序占点规则："]
    for k, v in AAS_RULES.items():
        lines.append(f"- {k}：{v}")
    return "\n".join(lines) + "\n" + COUNTERS


def _zones(init, tick):
    ordered = sorted([z for z in init.zones if z.order >= 0], key=lambda z: z.order)
    if not ordered:
        return "这张图没有顺序占点据点"
    caps = tick.caps if tick is not None else []
    lines = [f"据点（共 {len(ordered)} 个，按链序 order，本地坐标）："]
    for z in ordered:
        if z.circle:
            cx, cz = init.to_local(z.a[0], z.a[1])
            where = f"圆心({cx:.0f},{cz:.0f}) 半径{z.a[2]:.0f}米"
        else:
            x0, z0 = init.to_local(z.a[0], z.a[1])
            x1, z1 = init.to_local(z.a[2], z.a[3])
            where = f"({x0:.0f},{z0:.0f})到({x1:.0f},{z1:.0f})"
        owner = "中立"
        if z.order < len(caps):
            o, timer = caps[z.order]
            if o == init.seat:
                owner = "我"
            elif o >= 0:
                owner = "敌"
            if timer > 0:
                owner += f"（占领中 {min(100, int(timer * 100 / (10 * init.hz)))}%）"
        lines.append(f"- {z.name} [order {z.order}] {where} → {owner}")
    return "\n".join(lines)
