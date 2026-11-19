# -*- coding: utf-8 -*-
"""把 TickState 压成 LLM 可读的战报文本（按「组」列出，不是逐个兵）。"""
import protocol as P
import squads

PROP_ZH = {"snowball": "雪球", "keg": "火药桶", "flag": "军旗", "barrier": "木栅", "gate": "木栅门", "pillar": "立石"}

MODE_ZH = {"duel": "1v1 对战", "melee": "四人混战", "level": "闯关", "sandbox": "测试"}


def _group_by_kind(units):
    out = {}
    for u in units:
        out.setdefault(squads.kind_of(u), []).append(u)
    return out


def _squad_line(kind, units):
    n = len(units)
    cx = sum(u.x for u in units) / n
    cz = sum(u.z for u in units) / n
    zh = squads.kind_zh(kind)
    fl = ""
    if any(u.braced for u in units):
        fl += " 架枪"
    if any(u.flags & 32 for u in units):
        fl += " 冲锋"
    if any(u.holding for u in units):
        fl += " 守"
    return f"  {kind}({zh}) {n}人 中心({cx:.1f},{cz:.1f}){fl}"


def build_summary(init, st):
    lines = []
    lines.append(f"【第 {st.time:.1f} 秒 · 回合 {st.tick} · {MODE_ZH.get(init.mode, init.mode)} · {init.map_name} · 时限 {init.time_limit:.0f} 秒】")

    # 目标
    if init.mode == "duel":
        lines.append("目标：全歼对方；时间到比计分区里活兵的总造价。")
        if init.score_zone:
            lines.append(f"计分区「{init.score_zone}」；守住计分区里的兵越多越有利。")
    elif init.mode == "melee":
        lines.append("目标：四人混战，尽可能多杀、活到最后；名次和歼敌造价计分。")
    else:
        lines.append("目标：达成本关过关条件。")

    # 我方
    mine = [u for u in st.units if u.alive and u.mine]
    enemy = [u for u in st.units if u.alive and not u.mine]
    cost = sum(init.defs[u.kind].cost for u in mine if u.kind in init.defs)
    mgroups = _group_by_kind(mine)
    lines.append(f"我方 {len(mgroups)} 组（共 {len(mine)} 人，造价 {cost:.0f}）：")
    for kind, units in mgroups.items():
        lines.append(_squad_line(kind, units))
    if not mine:
        lines.append("  （已全灭）")

    # 敌方（迷雾：只列可见的）
    egroups = _group_by_kind(enemy)
    lines.append(f"敌方可见 {len(egroups)} 组（共 {len(enemy)} 人）：")
    for kind, units in egroups.items():
        lines.append(_squad_line(kind, units))
    if not enemy:
        lines.append("  （无可见敌人，最后目击见下）")

    # 最后目击（迷雾里看不见的敌方单位）
    if st.ghosts:
        lines.append("最后目击：")
        for g in st.ghosts:
            us = init.units.get(g.id)
            nm = P.KIND_EN[us.kind] if (us is not None and 0 <= us.kind < len(P.KIND_EN)) else "?"
            age = max(0.0, st.time - g.step / float(init.hz))
            lines.append(f"  [幽灵] {nm} ({g.x:.1f},{g.z:.1f}) {age:.0f} 秒前")

    # 道具
    alive_props = [pr for pr in st.props if pr.alive]
    if alive_props:
        lines.append("道具：")
        for pr in alive_props:
            team = "我" if pr.team == init.team else ("敌" if pr.team >= 0 else "中")
            extra = f" 已触发" if pr.triggered else ""
            lines.append(f"  #{pr.id} {PROP_ZH.get(pr.type, pr.type)}({team}) ({pr.x:.1f},{pr.z:.1f}) 血{pr.hp:.0f}{extra}")

    # 镇山兽
    if st.bosses:
        lines.append("镇山兽：")
        boss_state = ["游走", "刨地", "冲撞", "晕"]
        for b in st.bosses:
            lines.append(f"  #{b.id} {boss_state[b.state] if 0 <= b.state < 4 else b.state}")

    # 水面
    if init.water["kind"] == 0:
        pass
    elif st.water > -1e8:
        lines.append(f"水面高度 {st.water:.1f} 米（高于单位脚底会淹死）")

    # 近期事件
    if st.events:
        interesting = [e for e in st.events if any(k in e for k in ("death", "destroyed", "terrain_removed", "boss", "sortie", "rally_go", "released", "explode", "eliminated"))]
        if interesting:
            lines.append("近况：" + "；".join(interesting[-8:]))

    return "\n".join(lines)


DEFAULT_SYSTEM_PROMPT = """你是《纸上谈兵》的指挥官，指挥一队士兵（分成若干组）和另一队 AI 对战。

坐标系：本地视角，x 向右、z 朝敌人，单位米，原点在你的布阵区。每回合（8 秒）收到一次战报，按「组」列出（兵种英文名/中文名/人数/中心坐标/状态）。请给出指令，每行一条，可选：
  move <兵种> <x> <z>       整组走到某点（编队、自动绕路）
  attack <兵种> <目标>      整组追打（目标可以是兵种英文名或编号）
  hold <兵种>               整组原地守（长枪兵站定架枪）
  free <兵种>               整组自由行动（默认行为）
  aim <兵种> <x> <z>        整组朝地点盲射（弓手/投石车）
  detonate <兵种>           整组引爆（炮仗兵）
  push <兵种> <雪球编号>     整组推雪球

「兵种」用英文名：militia(新兵) shield(盾卫) spear(长枪兵) archer(弓手) cavalry(骑兵) hammer(大锤) bomber(炮仗兵) catapult(投石车) giant(大块头) boss(镇山兽) lord(主帅)；也可以填单个兵编号精确指挥。

要点：占高地、集火残血、卡住计分区、骑兵冲弓手、枪兵顶骑兵、弓手远离近战。没提到的组保持现状。

每轮战报上方会附【作战计划】和【战况史】，是你之前几轮的记忆，先读它们、保持战略连续，再决策。

回复格式：
1. （可选）更新作战计划：先写一行 [计划]，换行写 1~3 句新计划（战术目标/集火对象/阵地策略），再写一行 [/计划]。有变化才更新，没变化可省略整段。
2. 写 1~3 句中文决策思路（简述你看到什么、打算怎么打、为什么）。
3. 逐行输出指令，英文动词开头，每行一条，只写要改动的组。

不要输出其他内容。"""


def build_system_prompt(init, custom):
    """返回 system prompt：自定义优先，否则默认 + 地图概况。"""
    base = custom if custom else DEFAULT_SYSTEM_PROMPT
    # 追加地图概况（区域/计分区，本地坐标已换算成布阵视角）
    bits = []
    if init.score_zone:
        bits.append(f"计分区「{init.score_zone}」")
    for z in init.zones:
        if z.name == init.score_zone:
            continue
        if z.circle:
            cx, cz = init.to_local(z.a[0], z.a[1])
            bits.append(f"区域「{z.name}」圆心({cx:.0f},{cz:.0f}) 半径{z.a[2]:.0f}米")
        else:
            x0, z0 = init.to_local(z.a[0], z.a[1])
            x1, z1 = init.to_local(z.a[2], z.a[3])
            bits.append(f"区域「{z.name}」({x0:.0f},{z0:.0f})到({x1:.0f},{z1:.0f})")
    if bits:
        base += "\n地图区域（本地坐标）：" + "；".join(bits) + "。"
    return base
