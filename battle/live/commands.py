# -*- coding: utf-8 -*-
"""把 LLM 的回复解析成引擎协议的指令 token，并拼出每回合的输出行。

LLM 输出：先写决策思路（中文，若干行），再逐行输出指令（英文动词开头）：
    对方骑兵多，我让长枪兵架枪守住山头。
    move 3 5.0 -10.0
    hold 7
    attack 12 34
    free 9

指令编码成协议 token（世界坐标厘米），对应 commander.hpp 的 pt()/op()：
    move -> M id wx wz     attack -> A id target
    hold -> H id           free   -> F id
    aim  -> P id wx wz     detonate -> D id
    push -> U id target
"""
import protocol as P

COMMAND_OPS = {"move", "movestraight", "attack", "hold", "free", "aim", "detonate", "push"}


def parse_llm(text, tick, init):
    """返回 (tokens, errors, reasoning)。

    - tokens：协议 token 列表，如 ['M 12 530 -1200', 'A 3 25']
    - errors：指令解析/校验失败的行
    - reasoning：非指令行（LLM 的决策思路），原样收集

    规则：首词是已知指令动词的行按指令解析；其余行当作决策思路，不报错。
    """
    tokens = []
    errors = []
    reasoning = []
    alive_mine = {u.id for u in tick.units if u.alive and u.mine}
    alive_any = {u.id for u in tick.units if u.alive}
    alive_prop = {pr.id for pr in tick.props if pr.alive}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 去掉列表前缀（编号、Markdown 符号），便于识别 "1. move 3 5"
        probe = line.lstrip(">-#*0123456789. \t")
        parts = probe.split()
        if not parts:
            reasoning.append(line)
            continue
        op = parts[0].lower()
        if op not in COMMAND_OPS:
            reasoning.append(line)
            continue
        try:
            if op in ("move", "movestraight"):
                uid, x, z = int(parts[1]), float(parts[2]), float(parts[3])
                if uid not in alive_mine:
                    errors.append(f"move: 单位 {uid} 不是我的活单位")
                    continue
                wx, wz = init.to_world(x, z)
                tok = "L" if op == "movestraight" else "M"
                tokens.append(f"{tok} {uid} {round(wx * 100)} {round(wz * 100)}")
            elif op == "attack":
                uid, target = int(parts[1]), int(parts[2])
                if uid not in alive_mine:
                    errors.append(f"attack: 单位 {uid} 不是我的活单位")
                    continue
                if target not in alive_any and target not in alive_prop:
                    errors.append(f"attack: 目标 {target} 不存在或已死")
                    continue
                tokens.append(f"A {uid} {target}")
            elif op == "hold":
                uid = int(parts[1])
                if uid not in alive_mine:
                    errors.append(f"hold: 单位 {uid} 不是我的活单位")
                    continue
                tokens.append(f"H {uid}")
            elif op == "free":
                uid = int(parts[1])
                if uid not in alive_mine:
                    errors.append(f"free: 单位 {uid} 不是我的活单位")
                    continue
                tokens.append(f"F {uid}")
            elif op == "aim":
                uid, x, z = int(parts[1]), float(parts[2]), float(parts[3])
                if uid not in alive_mine:
                    errors.append(f"aim: 单位 {uid} 不是我的活单位")
                    continue
                wx, wz = init.to_world(x, z)
                tokens.append(f"P {uid} {round(wx * 100)} {round(wz * 100)}")
            elif op == "detonate":
                uid = int(parts[1])
                if uid not in alive_mine:
                    errors.append(f"detonate: 单位 {uid} 不是我的活单位")
                    continue
                tokens.append(f"D {uid}")
            elif op == "push":
                uid, target = int(parts[1]), int(parts[2])
                if uid not in alive_mine:
                    errors.append(f"push: 单位 {uid} 不是我的活单位")
                    continue
                if target not in alive_prop:
                    errors.append(f"push: 目标 {target} 不是活道具（雪球）")
                    continue
                tokens.append(f"U {uid} {target}")
        except (ValueError, IndexError):
            errors.append(f"解析失败：{line}")
    return tokens, errors, reasoning


def make_line(tick, tokens):
    """拼出每回合输出行：'<tick> M ... A ...'（无指令时只有 '<tick>'）。"""
    if tokens:
        return str(tick) + " " + " ".join(tokens)
    return str(tick)
