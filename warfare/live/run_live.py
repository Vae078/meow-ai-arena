#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入口：跑一场 1v1 对战，两个座位的指挥官各配一种。

每个座位可以是 AI 桥接（LLM），也可以是编译好的确定性程序（.cpp/.exe），或不用程序（-）。

用法：
  python run_live.py --map 樱丘 --deploy 甲.json 乙.json --ai 甲.json 乙.json [-o out/]     AI vs AI
  python run_live.py --map 樱丘 --deploy 甲.json 乙.json --ai 甲.json commander.cpp          AI vs 确定性程序
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
WAR = REPO / "warfare" / "workspace" / "war.exe"
MAPS = REPO / "warfare" / "workspace" / "maps"
sys.path.insert(0, str(REPO / "warfare" / "workspace"))
import build  # noqa: E402  编译确定性指挥官 .cpp 用
import draft  # noqa: E402  战前征召
import providers  # noqa: E402


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


def resolve_commander(spec):
    """一个座位的指挥官 -> 传给 war.exe 的 --cmd。

    - '-'           不用程序（布阵姿态 + 默认行为）
    - '*.json'      AI 配置，接 ai_commander.py（LLM 桥接）
    - '*.cpp'       编译成 exe 再用（确定性程序）
    - '*.exe'       直接用
    """
    spec = str(spec)
    if spec == "-":
        return "-"
    p = Path(spec).resolve()
    if not p.is_file():
        sys.exit(f"找不到指挥官：{spec}")
    if p.suffix.lower() == ".json":
        return ai_cmd(str(p))
    if p.suffix.lower() == ".cpp":
        exe, err = build.build(p)
        if err:
            sys.exit(err)
        return str(exe)
    if p.suffix.lower() == ".exe":
        return str(p)
    sys.exit(f"不认识的指挥官（要 .json / .cpp / .exe 或 -）：{spec}")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="AI 实时对战（1v1）")
    ap.add_argument("--map", default="樱丘")
    ap.add_argument("--deploy", nargs="*", default=[], help="两份布阵 json（可省；省了由 LLM 开局征召）")
    ap.add_argument("--ai", nargs=2, required=True,
                    help="两个座位的指挥官（甲 乙）：AI 配置 .json，或确定性程序 .cpp/.exe，或 - 不用程序")
    ap.add_argument("-o", "--out", default=str(HERE / "out"))
    args = ap.parse_args()

    if not WAR.is_file():
        sys.exit("找不到 war.exe，先运行 python warfare/build_engine.py 编译")

    mapf = resolve_map(args.map)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 布阵：--deploy 给了就用；没给的座位由 LLM 征召（确定性程序 / 无程序的座位给空布阵）
    map_data = json.load(open(mapf, encoding="utf-8"))
    seats = map_data.get("seats", [])
    deploy = []
    for i in range(2):
        if i < len(args.deploy):
            deploy.append(str(Path(args.deploy[i]).resolve()))
            continue
        dp = out / ("deploy_%d.json" % i)
        spec = args.ai[i]
        if Path(spec).suffix == ".json":
            cfg = json.load(open(spec, encoding="utf-8"))
            provider = providers.make_provider(cfg)
            sdef = seats[i] if i < len(seats) else {}
            budget = int(sdef.get("budget", 1000))
            rect = (sdef.get("deploy") or [[0, 0, 16, 10]])[0]
            prompt = draft.build_draft_prompt(map_data.get("name", ""), budget)
            reply = provider.chat(prompt, "请征召你的开局阵容。")
            units, errs, total = draft.parse_draft(reply, budget)
            for e in errs:
                print(f"[座位{i} 征召] {e}", flush=True)
            print(f"[座位{i} 征召] 造价{total} {units}", flush=True)
            json.dump(draft.make_deploy(units, rect), open(dp, "w", encoding="utf-8"), ensure_ascii=False)
        else:
            json.dump({"units": []}, open(dp, "w", encoding="utf-8"))
        deploy.append(str(dp.resolve()))

    cmds = [resolve_commander(x) for x in args.ai]

    replay_dir = out / "replays"
    log_dir = out / "logs"
    replay_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    replay = replay_dir / "last.wrp"
    result = out / "result.json"

    cmd = [str(WAR), "run", str(mapf),
           "--deploy", deploy[0], "--deploy", deploy[1],
           "--cmd", cmds[0], "--cmd", cmds[1],
           "--wall-ms", "180000", "--first-wall-ms", "180000",
           "--step-ms", "5000", "--first-ms", "10000",
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
        print(f"  看复盘：python warfare/workspace/play.py replay {replay}")
        print(f"日志目录：{log_dir}")
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
