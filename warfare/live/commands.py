# -*- coding: utf-8 -*-
"""把 LLM 的回复解析成引擎协议的指令 token，并拼出每回合的输出行。

LLM 输出：先写决策思路（中文，若干行），再逐行输出指令（英文动词开头）。
指令的「源」可以是：
  - 单个兵编号（move 3 5.0 -10.0）
  - 兵种英文名（move archer 5.0 -10.0）→ 整组编队行动（扇出成组内每个兵）

指令编码成协议 token（世界坐标厘米），对应 commander.hpp 的 pt()/op()：
    move -> M id wx wz     attack -> A id target
    hold -> H id           free   -> F id
    aim  -> P id wx wz     detonate -> D id
    push -> U id target
"""
import protocol as P
import squads

COMMAND_OPS = {"move", "movestraight", "attack", "hold", "free", "aim", "detonate", "push", "buy"}


def parse_llm(text, tick, init):
    """返回 (tokens, errors, reasoning)。

    - tokens：协议 token 列表，如 ['M 12 530 -1200', 'A 3 25']
    - errors：指令解析/校验失败的行
    - reasoning：非指令行（LLM 的决策思路），原样收集

    规则：首词是已知指令动词的行按指令解析；其余行当作决策思路，不报错。
    源可以是兵编号或兵种名；兵种名会扇出成整组。
    """
    tokens = []
    errors = []
    reasoning = []
    units_by_id = {u.id: u for u in tick.units}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 去掉列表前缀（编号、Markdown 符号），便于识别 "1. move archer 5"
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
                src, err = squads.resolve_mine(tick, parts[1])
                if err:
                    errors.append(f"move: {err}")
                    continue
                x, z = float(parts[2]), float(parts[3])
                offs = squads.spread(len(src))
                for i, uid in enumerate(src):
                    dx, dz = offs[i]
                    wx, wz = init.to_world(x + dx, z + dz)
                    tok = "L" if op == "movestraight" else "M"
                    tokens.append(f"{tok} {uid} {round(wx * 100)} {round(wz * 100)}")
            elif op == "attack":
                src, err = squads.resolve_mine(tick, parts[1])
                if err:
                    errors.append(f"attack: {err}")
                    continue
                tgt, err2 = squads.resolve_enemy(tick, parts[2])
                if err2:
                    errors.append(f"attack: {err2}")
                    continue
                assign = squads.assign_targets([units_by_id[i] for i in src], tgt, tick)
                for uid in src:
                    tokens.append(f"A {uid} {assign[uid]}")
            elif op == "hold":
                src, err = squads.resolve_mine(tick, parts[1])
                if err:
                    errors.append(f"hold: {err}")
                    continue
                for uid in src:
                    tokens.append(f"H {uid}")
            elif op == "free":
                src, err = squads.resolve_mine(tick, parts[1])
                if err:
                    errors.append(f"free: {err}")
                    continue
                for uid in src:
                    tokens.append(f"F {uid}")
            elif op == "aim":
                src, err = squads.resolve_mine(tick, parts[1])
                if err:
                    errors.append(f"aim: {err}")
                    continue
                x, z = float(parts[2]), float(parts[3])
                wx, wz = init.to_world(x, z)
                for uid in src:
                    tokens.append(f"P {uid} {round(wx * 100)} {round(wz * 100)}")
            elif op == "detonate":
                src, err = squads.resolve_mine(tick, parts[1])
                if err:
                    errors.append(f"detonate: {err}")
                    continue
                for uid in src:
                    tokens.append(f"D {uid}")
            elif op == "push":
                src, err = squads.resolve_mine(tick, parts[1])
                if err:
                    errors.append(f"push: {err}")
                    continue
                tgt, err2 = squads.resolve_enemy(tick, parts[2])
                if err2:
                    errors.append(f"push: {err2}")
                    continue
                for uid in src:
                    tokens.append(f"U {uid} {tgt[0]}")
            elif op == "buy":
                s = parts[1]
                if s not in P.KIND_EN:
                    errors.append(f"buy: 不认识的兵种 {s}")
                    continue
                tokens.append(f"B {P.KIND_EN.index(s)}")
        except (ValueError, IndexError):
            errors.append(f"解析失败：{line}")
    return tokens, errors, reasoning


def make_line(tick, tokens):
    """拼出每回合输出行：'<tick> M ... A ...'（无指令时只有 '<tick>'）。"""
    if tokens:
        return str(tick) + " " + " ".join(tokens)
    return str(tick)
