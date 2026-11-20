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
    if st.caps:
        # AAS 顺序占点
        ordered = sorted([z for z in init.zones if z.order >= 0], key=lambda z: z.order)
        parts = []
        for i, z in enumerate(ordered):
            if i >= len(st.caps):
                break
            owner, timer = st.caps[i]
            if owner == init.seat:
                tag = "我"
            elif owner >= 0:
                tag = "敌"
            else:
                tag = "中立"
            if timer > 0:
                pct = int(min(100, timer * 100 / (10 * init.hz)))
                tag += f"←占{pct}%"
            parts.append(f"{z.name}[{tag}]")
        lines.append("据点（南→北）：" + " ".join(parts))
        my_res = st.res[init.seat] if init.seat < len(st.res) else 0
        lines.append(f"兵力(ticket)：我 {my_res}（可 buy 组）")
        lines.append("目标：顺序占点，占满全图或耗尽敌方兵力取胜。")
    else:
        if st.score:
            mine = st.score[init.seat] if init.seat < len(st.score) else 0
            other = sum(st.score) - mine
            lines.append(f"得分：我 {mine} : 敌 {other}")
        if st.res:
            my_res = st.res[init.seat] if init.seat < len(st.res) else 0
            lines.append(f"增援点：我 {my_res}（可 buy 组）")
        # 目标
        if init.mode == "duel":
            lines.append("目标：占领据点得分（每回合据点归属方得分），时间到分高者胜；全歼对方立即胜。")
            if init.score_zone:
                lines.append(f"计分区「{init.score_zone}」；守住计分区里的兵越多越有利。")
        elif init.mode == "melee":
            lines.append("目标：四人混战，尽可能多杀、活到最后；名次和歼敌造价计分。")
        else:
            lines.append("目标：达成本关过关条件。")

    # 我方 / 敌方（优先用引擎的组实体 st.squads，组 id 可直接指挥）
    ORD_ZH = ["自由", "驻守", "移动", "攻击"]
    if st.squads:
        mine_sq = [sq for sq in st.squads if sq.seat == init.seat]
        enemy_sq = [sq for sq in st.squads if sq.seat != init.seat]
        def sq_line(sq):
            en = P.KIND_EN[sq.kind] if 0 <= sq.kind < len(P.KIND_EN) else str(sq.kind)
            zh = squads.kind_zh(en)
            st2 = ORD_ZH[sq.ord] if 0 <= sq.ord < len(ORD_ZH) else ""
            return f"  #{sq.id} {en}({zh}) {sq.alive}人 中心({sq.x:.1f},{sq.z:.1f}) {st2}"
        lines.append(f"我方 {len(mine_sq)} 组：")
        for sq in mine_sq:
            lines.append(sq_line(sq))
        if not mine_sq:
            lines.append("  （已全灭）")
        lines.append(f"敌方可见 {len(enemy_sq)} 组：")
        for sq in enemy_sq:
            lines.append(sq_line(sq))
        if not enemy_sq:
            lines.append("  （无可见敌人，最后目击见下）")
    else:
        mine = [u for u in st.units if u.alive and u.mine]
        enemy = [u for u in st.units if u.alive and not u.mine]
        cost = sum(init.defs[u.kind].cost for u in mine if u.kind in init.defs)
        mgroups = _group_by_kind(mine)
        lines.append(f"我方 {len(mgroups)} 组（共 {len(mine)} 人，造价 {cost:.0f}）：")
        for kind, units in mgroups.items():
            lines.append(_squad_line(kind, units))
        if not mine:
            lines.append("  （已全灭）")
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

坐标系：本地视角，x 向右、z 朝敌人，单位米，原点在你的布阵区。每回合（8 秒）收到一次战报，按「组」列出（组id/兵种/人数/中心/状态）。指令对「组」下，用战报里的组 id（#数字），可选：
  squad_move <组id> <x> <z>      整组走到某点（组内自动编队）
  squad_attack <组id> <目标组id> 整组追打目标组（组内自动分配目标）
  squad_hold <组id>              整组原地守（长枪兵架枪）
  squad_free <组id>              整组自由行动（默认行为）
  buy <兵种>                     花 ticket 在基地买一整组

「组id」看战报「我方 N 组」下的 #数字；buy 的兵种用英文名：militia(新兵) shield(盾卫) spear(长枪兵) archer(弓手) cavalry(骑兵) hammer(大锤) bomber(炮仗兵) catapult(投石车) giant(大块头)。也可对单个兵编号用 move/attack/aim/detonate/push 微操。

要点：顺序占点（只能占与自己已占点相邻的点，占点要派兵站进去守够时间）、守住前线点、集火残血、骑兵冲弓手、枪兵顶骑兵、弓手远离近战。没提到的组保持现状。

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
        if z.score_rate > 0:
            if z.circle:
                cx, cz = init.to_local(z.a[0], z.a[1])
                bits.append(f"据点「{z.name}」圆心({cx:.0f},{cz:.0f}) 半径{z.a[2]:.0f}米 值{z.score_rate:.0f}分")
            else:
                x0, z0 = init.to_local(z.a[0], z.a[1])
                x1, z1 = init.to_local(z.a[2], z.a[3])
                bits.append(f"据点「{z.name}」({x0:.0f},{z0:.0f})到({x1:.0f},{z1:.0f}) 值{z.score_rate:.0f}分")
        else:
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
