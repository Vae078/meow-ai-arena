# -*- coding: utf-8 -*-
"""组（squad）：同兵种的我方单位编成一组，作为 LLM 的唯一指挥单位。

LLM 对「组」下令（如 `move archer 5.0 -10.0`），本模块把组指令扇出成组内每个兵
的引擎指令。兵种名用英文（militia/shield/spear/archer/cavalry/hammer/bomber/
catapult/giant/boss/lord），战报里展示中文。
"""
import math

import protocol as P


def kind_of(u):
    return P.KIND_EN[u.kind] if 0 <= u.kind < len(P.KIND_EN) else str(u.kind)


def kind_zh(kind):
    i = P.KIND_EN.index(kind) if kind in P.KIND_EN else -1
    return P.KIND_ZH[i] if 0 <= i < len(P.KIND_ZH) else kind


def mine_by_kind(tick):
    """我方活单位按兵种分组：{兵种名: [Unit]}，只含有活单位的兵种。"""
    out = {}
    for u in tick.units:
        if u.alive and u.mine:
            out.setdefault(kind_of(u), []).append(u)
    return out


def resolve_mine(tick, spec):
    """指令里的「源」→ 我方活单位 id 列表 + 错误。整数=单个兵；兵种名=整组。"""
    s = str(spec)
    if s.lstrip("-").isdigit():
        uid = int(s)
        if any(u.id == uid and u.alive and u.mine for u in tick.units):
            return [uid], None
        return [], f"{uid} 不是我的活单位"
    if s in P.KIND_EN:
        ids = [u.id for u in tick.units if u.alive and u.mine and kind_of(u) == s]
        return ids, (None if ids else f"{s} 没有活单位")
    return [], f"不认识的源「{s}」（要兵编号或兵种英文名）"


def resolve_enemy(tick, spec):
    """指令里的「目标」→ 目标 id 列表 + 错误。整数=单位/道具；兵种名=该兵种所有敌方活单位。"""
    s = str(spec)
    if s.lstrip("-").isdigit():
        tid = int(s)
        if any(u.id == tid and u.alive for u in tick.units) or any(pr.id == tid and pr.alive for pr in tick.props):
            return [tid], None
        return [], f"目标 {tid} 不存在或已死"
    if s in P.KIND_EN:
        ids = [u.id for u in tick.units if u.alive and not u.mine and kind_of(u) == s]
        return ids, (None if ids else f"{s} 没有敌方活单位")
    return [], f"不认识的目标「{s}」（要编号或兵种英文名）"


def spread(n, spacing=1.6):
    """把 n 个单位排成近似方阵，返回 [(dx, dz)] 偏移，供 move 编队用。"""
    side = max(1, int(round(math.sqrt(n))))
    out = []
    for i in range(n):
        r, c = divmod(i, side)
        out.append(((c - (side - 1) / 2) * spacing, (r - (side - 1) / 2) * spacing))
    return out


def assign_targets(src_units, tgt_ids, tick):
    """每个源单位就近选一个目标（可重复），返回 {src_id: tgt_id}。"""
    pos = {u.id: (u.x, u.z) for u in tick.units}
    for pr in tick.props:
        pos[pr.id] = (pr.x, pr.z)

    def d2(a, b):
        return (pos.get(a, (0, 0))[0] - pos.get(b, (0, 0))[0]) ** 2 + (pos.get(a, (0, 0))[1] - pos.get(b, (0, 0))[1]) ** 2

    out = {}
    for u in src_units:
        out[u.id] = min(tgt_ids, key=lambda t: d2(u.id, t))
    return out
