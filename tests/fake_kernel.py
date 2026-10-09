#!/usr/bin/python3
"""模拟内核命令边界，记录真实脚本的调用，避免容器测试修改宿主内核。"""

import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["NEIHE_TEST_STATE"])
state = json.loads(state_path.read_text())
command = Path(sys.argv[0]).name
args = sys.argv[1:]
state["calls"].append([command, *args])


def finish(code=0, message=""):
    """在成功和失败分支都保存调用及内核状态。"""
    state_path.write_text(json.dumps(state))
    if message:
        print(message, file=sys.stderr if code else sys.stdout)
    sys.exit(code)


if command == "uname" and args == ["-s"]:
    finish(message=state["os"])
elif command == "modprobe" and len(args) == 1:
    module = args[0]
    if module not in {"tcp_bbr", "sch_fq"}:
        finish(64, "unexpected module")
    if module == state.get("missing_module"):
        finish(1, "module unavailable")
    state["modules"].append(module)
    if module == "tcp_bbr":
        state["available"] += " bbr"
    finish()
elif command == "sysctl":
    if args == ["-n", "net.ipv4.tcp_available_congestion_control"]:
        if state.get("read_error"):
            finish(1, "sysctl read failed")
        finish(message=state["available"])
    if len(args) == 2 and args[0] == "-p":
        state["loaded_file"] = args[1]
        if state.get("apply_error"):
            finish(1, "sysctl apply failed")
        # 消费脚本实际写出的文件；不是根据生产代码构造期望值。
        for line in Path(args[1]).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            key, value = [part.strip() for part in line.split("=", 1)]
            state["values"][key] = value
        finish()
elif command == "reboot":
    finish(99, "tests must never reboot")

finish(64, f"unexpected command: {command} {args}")
