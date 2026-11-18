#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 指挥桥接程序：作为 war.exe 的 commander 子进程运行。

stdin/stdout 说指挥协议，内部调 LLM。日志全写 stderr（引擎 --stderr-dir 会落盘）。
每 interval_secs 决策一次，其余回合回空行（= 保持上一条指令，士兵按默认行为打）。
"""
import argparse
import json
import sys

import commands
import protocol
import providers
import summary


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def main():
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    a = ap.parse_args()

    cfg = json.load(open(a.config, encoding="utf-8"))
    provider = providers.make_provider(cfg)
    interval_secs = float(cfg.get("decision_interval_secs", 3.0))
    interval_ticks = max(1, int(round(interval_secs * 10)))
    max_calls = int(cfg.get("max_calls_per_match", 60))
    system_custom = cfg.get("system_prompt", "")

    init = protocol.parse_init(sys.stdin)
    if init is None:
        log("[错误] 读不到 init 块")
        return 1
    system = summary.build_system_prompt(init, system_custom)
    log(f"[开局] 座位{init.seat} 模式{init.mode} 地图{init.map_name} "
        f"时限{init.time_limit:.0f}s 间隔{interval_secs}s 封顶{max_calls}次")

    call_count = 0
    while True:
        st = protocol.parse_tick(sys.stdin, init)
        if st is None:
            break

        for e in st.events:
            log(f"[t{st.tick}] {e}")

        tokens = []
        if st.tick % interval_ticks == 0 and call_count < max_calls:
            user = summary.build_summary(init, st)
            log(f"--- 决策 回合{st.tick}（第 {call_count + 1} 次调用）---")
            log(user)
            reply = None
            for attempt in range(2):  # 最多重试 1 次
                try:
                    reply = provider.chat(system, user)
                    call_count += 1
                    break
                except Exception as ex:
                    log(f"[调用失败 attempt={attempt}] {type(ex).__name__}: {ex}")
            if reply is None:
                log("跳过本次决策（调用失败），本回合空指令")
            else:
                tokens, errors, reasoning = commands.parse_llm(reply, st, init)
                if reasoning:
                    log("[决策思路]\n" + "\n".join(reasoning))
                for er in errors:
                    log(f"[非法指令] {er}")
                log(f"[生效指令] {' '.join(tokens) if tokens else '(无)'}")

        sys.stdout.write(commands.make_line(st.tick, tokens) + "\n")
        sys.stdout.flush()

    log(f"[结束] 共调用 LLM {call_count} 次")
    return 0


if __name__ == "__main__":
    sys.exit(main())
