#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入口：跑一场 AI 实时对战（1v1，两个 AI 桥接座位）。

用法：
  python run_live.py --map 樱丘 --deploy 甲.json 乙.json --ai 甲配置.json 乙配置.json [-o out/]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
WAR = REPO / "battle" / "workspace" / "war.exe"
MAPS = REPO / "battle" / "workspace" / "maps"


def resolve_map(name):
    p = Path(name)
    if p.is_file():
        return p
    m = MAPS / (name + ".json")
    if m.is_file():
        return m
    sys.exit(f"找不到地图：{name}（可写名字如 樱丘/河心洲，或直接路径）")


def ai_cmd(config_path):
    python = sys.executable
    script = HERE / "ai_commander.py"
    return f"cmd:{python} -u {script} --config {config_path}"


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="AI 实时对战（1v1）")
    ap.add_argument("--map", default="樱丘")
    ap.add_argument("--deploy", nargs=2, required=True, help="两份布阵 json（甲 乙）")
    ap.add_argument("--ai", nargs=2, required=True, help="两份 AI 配置 json（甲 乙）")
    ap.add_argument("-o", "--out", default=str(HERE / "out"))
    args = ap.parse_args()

    if not WAR.is_file():
        sys.exit("找不到 war.exe，先运行 python battle/build_engine.py 编译")

    mapf = resolve_map(args.map)
    deploy = [str(Path(x).resolve()) for x in args.deploy]
    config = [str(Path(x).resolve()) for x in args.ai]

    out = Path(args.out)
    replay_dir = out / "replays"
    log_dir = out / "logs"
    replay_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    replay = replay_dir / "last.wrp"
    result = out / "result.json"

    cmd = [str(WAR), "run", str(mapf),
           "--deploy", deploy[0], "--deploy", deploy[1],
           "--cmd", ai_cmd(config[0]), "--cmd", ai_cmd(config[1]),
           "--wall-ms", "180000", "--first-wall-ms", "180000",
           "--max-timeouts", "0", "--match-wall-s", "0",
           "--replay", str(replay), "--json", str(result),
           "--stderr-dir", str(log_dir)]
    print("$ " + " ".join(cmd), flush=True)
    p = subprocess.run(cmd)
    print(f"war.exe 退出码 {p.returncode}")

    if result.is_file():
        r = json.load(open(result, encoding="utf-8"))
        w = r.get("winner")
        reason = r.get("reason", "")
        if w is None:
            print(f"结果：{reason}")
        elif w < 0:
            print(f"结果：平局（{reason}）")
        else:
            print(f"结果：座位 {w}（{'甲' if w == 0 else '乙'}）获胜（{reason}）")
        print(f"回放：{replay}")
        print(f"  看复盘：python battle/workspace/play.py replay {replay}")
        print(f"日志目录：{log_dir}")
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
