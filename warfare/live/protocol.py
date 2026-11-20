# -*- coding: utf-8 -*-
"""指挥程序协议的解析（把 commander.hpp 的 read_init / read_tick 移植成 Python）。

只解析，不做任何 LLM 相关的事。引擎发给桥接程序的文本是：
  - init 块：一次，以 "END" 结束（含整张地图 GRID，这里直接丢弃）
  - tick 块：每 0.1s 一次，以 "E" 结束

坐标约定：引擎发来的是世界坐标（厘米）；桥接程序把单位位置转成本地坐标
（x 向右、z 朝敌人，和布阵文件一致），指令再转回世界坐标厘米发出去。
"""
import math


def _snap(v):
    """复刻 commander.hpp 里对 90 度整数倍朝向的精确化。"""
    if abs(v) < 1e-5:
        return 0.0
    if abs(v - 1.0) < 1e-5:
        return 1.0
    if abs(v + 1.0) < 1e-5:
        return -1.0
    return v


# 兵种编号 -> 中英文名（和 commander.hpp 的 enum Kind 一致）
KIND_ZH = ["新兵", "盾卫", "长枪兵", "弓手", "骑兵", "大锤", "炮仗兵", "投石车", "大块头", "镇山兽", "主帅"]
KIND_EN = ["militia", "shield", "spear", "archer", "cavalry", "hammer", "bomber", "catapult", "giant", "boss", "lord"]
STATE_ZH = ["站着", "倒地", "起身", "阵亡"]


class KindInfo:
    def __init__(self, id, cost, hp, dmg, cycle, windup, reach, speed, radius, climb):
        self.id = id
        self.cost = cost
        self.hp = hp
        self.dmg = dmg
        self.cycle = cycle
        self.windup = windup
        self.reach = reach
        self.speed = speed
        self.radius = radius
        self.climb = climb


class Zone:
    def __init__(self, name, circle, a, score_rate=0.0, order=-1):
        self.name = name
        self.circle = circle
        self.a = a  # circle: [cx, cz, r, 0]；rect: [x0, z0, x1, z1]
        self.score_rate = score_rate  # >0 = 据点，每回合归属方得这么多分
        self.order = order  # AAS 顺序占点：链上序号，-1 = 非顺序占点


class UnitStatic:
    def __init__(self, kind, team, seat, tag):
        self.kind = kind
        self.team = team
        self.seat = seat
        self.tag = tag


class PropStatic:
    def __init__(self, type_, team, x, y, z, tag):
        self.type = type_
        self.team = team
        self.x = x
        self.y = y
        self.z = z
        self.tag = tag


class InitState:
    def __init__(self):
        self.mode = ""
        self.map_name = ""
        self.seat = 0
        self.team = 0
        self.teams = 2
        self.seats = 2
        self.ox = 0.0
        self.oz = 0.0
        self.yaw = 0.0
        self.c = 1.0
        self.s = 0.0
        self.hz = 60
        self.time_limit = 90.0
        self.kill_y = -3.0
        self.defs = {}          # kind(int) -> KindInfo
        self.zones = []         # list[Zone]
        self.score_zone = ""
        self.water = {"kind": 0, "level": 0.0, "t0": 0.0, "t1": 0.0, "h0": 0.0, "h1": 0.0}
        self.units = {}         # id -> UnitStatic
        self.props = {}         # id -> PropStatic
        # GRID 只留边界（供坐标粗校验），不存整张高度图
        self.grid_w = 0
        self.grid_h = 0
        self.grid_x0 = 0.0
        self.grid_z0 = 0.0
        self.grid_cell = 0.5

    def to_world(self, x, z):
        return (self.ox + x * self.c + z * self.s, self.oz - x * self.s + z * self.c)

    def to_local(self, wx, wz):
        dx = wx - self.ox
        dz = wz - self.oz
        return (dx * self.c - dz * self.s, dx * self.s + dz * self.c)


class Unit:
    def __init__(self):
        self.id = 0
        self.kind = 0
        self.team = 0
        self.seat = -1
        self.state = 0
        self.alive = False
        self.x = 0.0      # 本地坐标
        self.y = 0.0
        self.z = 0.0
        self.vx = 0.0
        self.vz = 0.0
        self.hp = 0.0
        self.attacking = False
        self.cooldown = 0.0
        self.braced = False
        self.fuse = False
        self.holding = False
        self.facing = 0.0
        self.order = 0
        self.target = -1
        self.tag = ""
        self.mine = False
        self.flags = 0


class Prop:
    def __init__(self):
        self.id = 0
        self.type = ""
        self.team = -1
        self.alive = True
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.hp = 0.0
        self.triggered = False
        self.tag = ""


class Flying:
    def __init__(self):
        self.id = 0
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0
        self.land_x = 0.0
        self.land_z = 0.0


class BossInfo:
    def __init__(self):
        self.id = 0
        self.state = 0      # 0 游走 1 刨地 2 冲撞 3 晕
        self.dir_x = 0.0
        self.dir_z = 1.0
        self.next_charge = 0.0
        self.state_left = 0.0


class Ghost:
    """迷雾里不可见、但之前目击过的敌方单位（最后目击位置 + 步号）。"""
    def __init__(self):
        self.id = 0
        self.x = 0.0
        self.z = 0.0
        self.step = 0


class Squad:
    """组（squad）：LLM 的最小指挥单位。"""
    def __init__(self):
        self.id = 0          # 组 id（squad_move 等指令用）
        self.kind = 0        # 兵种编号
        self.seat = -1       # 座位（== init.seat 是己方组）
        self.alive = 0       # 组内活兵数
        self.x = 0.0         # 组中心（本地坐标）
        self.z = 0.0
        self.ord = 0         # 组状态：0 自由 1 驻守 2 移动 3 攻击


class TickState:
    def __init__(self):
        self.tick = 0
        self.time = 0.0
        self.water = -1e9
        self.units = []       # list[Unit]
        self.events = []      # list[str]
        self.bosses = []      # list[BossInfo]
        self.props = []       # list[Prop]
        self.flying = []      # list[Flying]
        self.ghosts = []      # list[Ghost]
        self.score = []       # 每队据点累计得分
        self.res = []         # 每队 ticket / 兵力
        self.caps = []        # AAS 顺序占点：[(归属, 占领进度步)]
        self.squads = []      # list[Squad]


def _readline(fin):
    """读一行，去换行符；EOF 返回 None。"""
    line = fin.readline()
    if line == "":
        return None
    line = line.rstrip("\n")
    if line.endswith("\r"):
        line = line[:-1]
    return line


def parse_init(fin):
    init = InitState()
    while True:
        line = _readline(fin)
        if line is None:
            return None
        if line == "END":
            return init
        if line.startswith("MODE "):
            init.mode = line[5:]
        elif line.startswith("MAP "):
            init.map_name = line[4:]
        elif line.startswith("SEAT "):
            p = line.split()
            init.seat = int(p[1])
            init.team = int(p[3])
            init.teams = int(p[5])
            init.seats = int(p[7])
        elif line.startswith("FRAME "):
            p = line.split()
            init.ox = float(p[1])
            init.oz = float(p[2])
            init.yaw = float(p[3])
            init.c = _snap(math.cos(init.yaw))
            init.s = _snap(math.sin(init.yaw))
        elif line.startswith("LIMIT "):
            p = line.split()
            init.hz = int(p[3])
            init.time_limit = float(p[1]) / float(init.hz)
        elif line.startswith("WATER "):
            p = line.split()
            if p[1] == "none":
                init.water["kind"] = 0
            elif p[1] == "static":
                init.water["kind"] = 1
                init.water["level"] = float(p[2])
            elif p[1] == "tide":
                init.water["kind"] = 2
                init.water["t0"] = float(p[2])
                init.water["t1"] = float(p[3])
                init.water["h0"] = float(p[4])
                init.water["h1"] = float(p[5])
        elif line.startswith("KILLY "):
            init.kill_y = float(line[6:])
        elif line.startswith("DEF "):
            p = line.split()
            k = int(p[1])
            init.defs[k] = KindInfo(p[2], float(p[3]), float(p[4]), float(p[5]), float(p[6]),
                                    float(p[7]), float(p[8]), float(p[9]), float(p[10]), float(p[11]))
        elif line.startswith("RULE "):
            pass  # 规则常数，摘要用不到
        elif line.startswith("GRID "):
            p = line.split()
            init.grid_w = int(p[1])
            init.grid_h = int(p[2])
            init.grid_x0 = float(p[3])
            init.grid_z0 = float(p[4])
            init.grid_cell = float(p[5])
            for _ in range(init.grid_h):  # 丢弃整张高度图
                _readline(fin)
        elif line.startswith("Z "):
            p = line.split()
            name = p[1]
            rate = float(p[p.index("score") + 1]) if "score" in p else 0.0
            order = int(p[p.index("order") + 1]) if "order" in p else -1
            if p[2] == "circle":
                init.zones.append(Zone(name, True, [float(p[3]), float(p[4]), float(p[5]), 0.0], rate, order))
            else:
                init.zones.append(Zone(name, False, [float(p[3]), float(p[4]), float(p[5]), float(p[6])], rate, order))
        elif line.startswith("SCORE "):
            s = line[6:]
            init.score_zone = "" if s == "-" else s
        elif line.startswith("UNITS "):
            n = int(line[6:])
            for _ in range(n):
                l = _readline(fin)
                p = l.split()
                init.units[int(p[0])] = UnitStatic(int(p[1]), int(p[2]), int(p[3]), "" if p[4] == "-" else p[4])
        elif line.startswith("PROPS "):
            n = int(line[6:])
            for _ in range(n):
                l = _readline(fin)
                p = l.split()
                init.props[int(p[0])] = PropStatic(p[1], int(p[2]), float(p[3]), float(p[4]), float(p[5]),
                                                   "" if p[6] == "-" else p[6])
        # 其余行忽略
    return init


def parse_tick(fin, init):
    line = _readline(fin)
    if line is None:
        return None
    st = TickState()
    p = line.split()
    st.tick = int(p[1])
    step = int(p[2])
    st.time = step / float(init.hz)
    wl = int(p[3])
    st.water = -1e9 if wl == -99999 else wl / 100.0
    nt = init.teams
    st.score = [int(x) for x in p[4:4 + nt]]          # 每队据点累计得分
    st.res = [int(x) for x in p[4 + nt:4 + 2 * nt]]   # 每队增援点

    # U 单位
    line = _readline(fin)
    n = int(line[2:])
    for _ in range(n):
        l = _readline(fin)
        v = [int(x) for x in l.split()]
        u = Unit()
        u.id = v[0]
        u.state = min(v[1], 3)
        u.alive = v[1] <= 2
        wx, wz = v[2] / 100.0, v[4] / 100.0
        u.x, u.z = init.to_local(wx, wz)
        u.y = v[3] / 100.0
        wvx, wvz = v[5] / 100.0, v[6] / 100.0
        u.vx = wvx * init.c - wvz * init.s
        u.vz = wvx * init.s + wvz * init.c
        u.hp = v[7] / 10.0
        u.attacking = v[8] < 0
        u.cooldown = 0.0 if v[8] < 0 else v[8] / float(init.hz)
        u.braced = (v[9] & 2) != 0
        u.fuse = (v[9] & 4) != 0
        u.holding = (v[9] & 8) != 0
        u.flags = v[9]
        u.facing = v[10] / 1000.0 - init.yaw
        u.order = v[11]
        u.target = v[12]
        s = init.units.get(u.id)
        if s is not None:
            u.kind = s.kind
            u.team = s.team
            u.seat = s.seat
            u.tag = s.tag
            u.mine = (s.seat == init.seat)
        st.units.append(u)

    # G 幽灵（迷雾里不可见但目击过的敌方单位）
    line = _readline(fin)
    n = int(line[2:])
    for _ in range(n):
        l = _readline(fin)
        v = [int(x) for x in l.split()]
        g = Ghost()
        g.id = v[0]
        wx, wz = v[1] / 100.0, v[2] / 100.0
        g.x, g.z = init.to_local(wx, wz)
        g.step = v[3]
        st.ghosts.append(g)

    # C 顺序占点：每点 归属 占领进度（步）
    line = _readline(fin)
    n = int(line[2:])
    for _ in range(n):
        l = _readline(fin)
        v = [int(x) for x in l.split()]
        st.caps.append((v[0], v[1]))

    # V 事件
    line = _readline(fin)
    n = int(line[2:])
    for _ in range(n):
        st.events.append(_readline(fin))

    # B 镇山兽
    line = _readline(fin)
    n = int(line[2:])
    for _ in range(n):
        l = _readline(fin)
        v = [int(x) for x in l.split()]
        b = BossInfo()
        b.id = v[0]
        b.state = v[1]
        wx, wz = v[2] / 1000.0, v[3] / 1000.0
        b.dir_x = wx * init.c - wz * init.s
        b.dir_z = wx * init.s + wz * init.c
        b.next_charge = v[4] / float(init.hz)
        b.state_left = (v[5] / 100.0) if b.state == 2 else (v[5] / float(init.hz))
        st.bosses.append(b)

    # P 道具
    line = _readline(fin)
    n = int(line[2:])
    for _ in range(n):
        l = _readline(fin)
        v = [int(x) for x in l.split()]
        pr = Prop()
        pr.id = v[0]
        pr.alive = v[1] != 0
        pr.x, pr.z = init.to_local(v[2] / 100.0, v[4] / 100.0)
        pr.y = v[3] / 100.0
        pr.hp = v[5] / 10.0
        pr.triggered = v[6] != 0
        ps = init.props.get(pr.id)
        if ps is not None:
            pr.type = ps.type
            pr.team = ps.team
            pr.tag = ps.tag
        st.props.append(pr)

    # S 飞行物（石头、雪球）
    line = _readline(fin)
    n = int(line[2:])
    for _ in range(n):
        l = _readline(fin)
        v = [int(x) for x in l.split()]
        f = Flying()
        f.id = v[0]
        f.x, f.z = init.to_local(v[1] / 100.0, v[3] / 100.0)
        f.y = v[2] / 100.0
        wvx, wvz = v[4] / 100.0, v[6] / 100.0
        f.vx = wvx * init.c - wvz * init.s
        f.vz = wvx * init.s + wvz * init.c
        f.vy = v[5] / 100.0
        f.land_x, f.land_z = init.to_local(v[7] / 100.0, v[8] / 100.0)
        st.flying.append(f)

    # Q 组状态：组 id 兵种 人数 中心x(cm) 中心z(cm) 状态
    line = _readline(fin)
    n = int(line[2:])
    for _ in range(n):
        l = _readline(fin)
        v = [int(x) for x in l.split()]
        sq = Squad()
        sq.id = v[0]
        sq.kind = v[1]
        sq.seat = v[2]
        sq.alive = v[3]
        sq.x, sq.z = init.to_local(v[4] / 100.0, v[5] / 100.0)
        sq.ord = v[6]
        st.squads.append(sq)

    _readline(fin)  # "E"
    return st
