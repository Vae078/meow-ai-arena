# -*- coding: utf-8 -*-
"""把 TickState 压成 LLM 可读的战报文本。"""
import protocol as P

PROP_ZH = {"snowball": "雪球", "keg": "火药桶", "flag": "军旗", "barrier": "木栅", "gate": "木栅门", "pillar": "立石"}

MODE_ZH = {"duel": "1v1 对战", "melee": "四人混战", "level": "闯关", "sandbox": "测试"}


def _flags(u):
    tags = []
    if u.braced:
        tags.append("架枪")
    if u.fuse:
        tags.append("点火")
    if u.flags & 32:
        tags.append("冲锋")
    if u.flags & 128:
        tags.append("腾空")
    if u.holding:
        tags.append("守")
    if u.attacking:
        tags.append("出手")
    return " ".join(tags)


def _unit_line(u):
    kind = P.KIND_ZH[u.kind] if 0 <= u.kind < len(P.KIND_ZH) else "?"
    fl = _flags(u)
    suffix = " " + fl if fl else ""
    return f"  #{u.id} {kind} ({u.x:.1f},{u.z:.1f}) 血{u.hp:.0f} {P.STATE_ZH[u.state]}{suffix}"


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
    lines.append(f"我方 {len(mine)} 个单位（造价 {cost:.0f}）：")
    for u in mine:
        lines.append(_unit_line(u))
    if not mine:
        lines.append("  （已全灭）")

    # 敌方
    lines.append(f"敌方 {len(enemy)} 个单位：")
    for u in enemy:
        lines.append(_unit_line(u))
    if not enemy:
        lines.append("  （已全灭）")

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


DEFAULT_SYSTEM_PROMPT = """你是《纸上谈兵》的指挥官，指挥一队士兵和另一队 AI 对战。

坐标系：本地视角，x 向右、z 朝敌人，单位米，原点在你的布阵区。每 3 秒收到一次战报（单位列表：编号/兵种/坐标/血量/状态）。请给出指令，每行一条，可选：
  move <兵编号> <x> <z>       走到某点（自动绕路）
  attack <兵编号> <目标编号>   追打目标（单位或道具）
  hold <兵编号>               原地守（长枪兵站定 0.5 秒架枪）
  free <兵编号>               自由行动（默认行为）
  aim <兵编号> <x> <z>        弓手/投石车朝地点盲射
  detonate <兵编号>           炮仗兵立即引爆
  push <兵编号> <雪球编号>     推雪球

兵种：新兵(便宜近战) 盾卫(血厚正面减伤) 长枪兵(架枪反骑兵) 弓手(远程) 骑兵(冲锋) 大锤(群伤) 炮仗兵(自爆) 投石车(远程群伤) 大块头(重装)。

要点：占高地、集火残血、卡住计分区、骑兵冲弓手、枪兵顶骑兵、弓手远离近战。没提到的兵保持现状。

每轮战报上方会附【作战计划】和【战况史】，是你之前几轮的记忆，先读它们、保持战略连续，再决策。

回复格式：
1. （可选）更新作战计划：先写一行 [计划]，换行写 1~3 句新计划（战术目标/集火对象/阵地策略），再写一行 [/计划]。有变化才更新，没变化可省略整段。
2. 写 1~3 句中文决策思路（简述你看到什么、打算怎么打、为什么）。
3. 逐行输出指令，英文动词开头，每行一条，只写要改动的兵。

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
