# -*- coding: utf-8 -*-
"""战前征召：让 LLM 自己决定开局兵组（组 = 最小单位），代替静态 --deploy 文件。

征召以「组」为单位，和战斗里的 buy 一致：`archer 2` = 两组弓手 = 24 个弓手。
"""
import math

# 兵种英文名 -> (中文名, 单体造价, 组人数)。和引擎 defs.hpp 的 DEFS + squad_size 一致
UNIT_TABLE = [
    ("militia", "新兵", 10, 12),
    ("shield", "盾卫", 25, 8),
    ("spear", "长枪兵", 25, 8),
    ("archer", "弓手", 30, 12),
    ("cavalry", "骑兵", 80, 6),
    ("hammer", "大锤", 40, 6),
    ("bomber", "炮仗兵", 35, 6),
    ("catapult", "投石车", 120, 2),
    ("giant", "大块头", 350, 1),
]
RANGED = {"archer", "catapult"}


def build_draft_prompt(map_name, budget):
    rows = "\n".join(
        f"  {en}({zh}) 每组{size}人 组价{cost * size}"
        for en, zh, cost, size in UNIT_TABLE
    )
    return (
        f"你是《纸上谈兵》的指挥官，先在「{map_name}」征召开局兵组。\n\n"
        f"最小单位是「组」（一队同兵种士兵）。可征兵组（英文名 中文名 组人数 组价）：\n{rows}\n\n"
        f"预算：{budget}。请列出你要征召的兵组，每行一个兵种，格式：<兵种英文名> <组数>。"
        f"总组价不超过预算。只输出征召行。"
    )


def parse_draft(text, budget):
    """把 LLM 回复解析成 [(兵种, 组数)]，校验预算。返回 (squads, errors, total)。"""
    cost = {en: c * n for en, _, c, n in UNIT_TABLE}  # 组价
    squads = []
    errors = []
    for raw in text.splitlines():
        line = raw.strip().lstrip(">-#*0123456789. \t")
        parts = line.split()
        if not parts:
            continue
        en = parts[0].lower()
        if en not in cost:
            continue  # 非征召行（思路、解释），忽略
        try:
            k = int(parts[1])
        except (ValueError, IndexError):
            errors.append(f"组数看不懂：{line}")
            continue
        if k > 0:
            squads.append((en, k))
    total = sum(cost[en] * k for en, k in squads)
    if total > budget + 1e-6:
        errors.append(f"总组价 {total} 超过预算 {budget}")
    if not squads:
        errors.append("没有征召任何兵组")
    return squads, errors, total


def make_deploy(squads, rect):
    """把 [(兵种, 组数)] 展开成一个个兵，摆进部署区 rect=[x0,z0,x1,z1]。近战在前、远程在后。"""
    x0, z0, x1, z1 = rect
    w, d = x1 - x0, z1 - z0
    size = {en: n for en, _, _, n in UNIT_TABLE}
    flat = []
    for en, k in squads:
        flat += [en] * (size[en] * k)
    flat.sort(key=lambda en: 0 if en in RANGED else 1)  # 远程(0)在前=靠基地
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
