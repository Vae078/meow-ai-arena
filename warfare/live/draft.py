# -*- coding: utf-8 -*-
"""战前征召：让 LLM 自己决定开局阵容，代替静态 --deploy 文件。

流程：用 AI 配置里的 provider 问 LLM 一句"预算 X，征召什么兵"，把回复解析成
[(兵种, 数量)]，再在部署区里排成方阵，生成一份 deploy dict。
"""
import math

# 兵种英文名 -> (中文名, 造价)。和引擎 defs.hpp 的 DEFS 一致（不含 boss/lord，不可征召）
UNIT_TABLE = [
    ("militia", "新兵", 10), ("shield", "盾卫", 25), ("spear", "长枪兵", 25),
    ("archer", "弓手", 30), ("cavalry", "骑兵", 80), ("hammer", "大锤", 40),
    ("bomber", "炮仗兵", 35), ("catapult", "投石车", 120), ("giant", "大块头", 350),
]
RANGED = {"archer", "catapult"}


def build_draft_prompt(map_name, budget):
    rows = "\n".join(f"  {en}({zh}) 造价{cost}" for en, zh, cost in UNIT_TABLE)
    return (
        f"你是《纸上谈兵》的指挥官，先在「{map_name}」征召开局阵容。\n\n"
        f"可征兵种（英文名 中文名 造价）：\n{rows}\n\n"
        f"预算：{budget}。请列出你要征召的兵，每行一个兵种，格式：<兵种英文名> <数量>。"
        f"总造价不超过预算。只输出征兵行。"
    )


def parse_draft(text, budget):
    """把 LLM 回复解析成 [(兵种, 数量)]，并校验预算。返回 (units, errors, total)。"""
    cost = {en: c for en, _, c in UNIT_TABLE}
    units = []
    errors = []
    for raw in text.splitlines():
        line = raw.strip().lstrip(">-#*0123456789. \t")
        parts = line.split()
        if not parts:
            continue
        en = parts[0].lower()
        if en not in cost:
            continue  # 非征兵行（思路、解释），忽略
        try:
            n = int(parts[1])
        except (ValueError, IndexError):
            errors.append(f"数量看不懂：{line}")
            continue
        if n > 0:
            units.append((en, n))
    total = sum(cost[en] * n for en, n in units)
    if total > budget + 1e-6:
        errors.append(f"总造价 {total} 超过预算 {budget}")
    if not units:
        errors.append("没有征召任何兵")
    return units, errors, total


def make_deploy(units, rect):
    """把 [(兵种, 数量)] 摆进部署区 rect=[x0,z0,x1,z1]，返回 deploy dict。

    近战在前（z 大，靠敌），远程在后（z 小，靠基地）。
    """
    x0, z0, x1, z1 = rect
    w, d = x1 - x0, z1 - z0
    flat = []
    for en, n in units:
        flat += [en] * n
    flat.sort(key=lambda k: 0 if k in RANGED else 1)  # 远程(0)在前=靠后
    n = len(flat)
    if n == 0:
        return {"units": []}
    cols = max(1, int(math.ceil(math.sqrt(n * max(w, 1.0) / max(d, 1.0)))))
    rows = max(1, int(math.ceil(n / cols)))
    out = []
    for i, en in enumerate(flat):
        r, c = divmod(i, cols)
        x = x0 + (c + 0.5) * w / cols
        z = z0 + (r + 0.5) * d / rows
        # 引擎要求坐标是 0.5 的整数倍（导航格子 0.5 米）
        out.append({"type": en, "x": round(x * 2) / 2, "z": round(z * 2) / 2})
    return {"units": out}
