"""SDD 工作区工具(禁 git 环境替代方案)。用后随工作区一起清理。

用法:
  python workspace_tools.py briefs            # 按 '### Task N' 切分计划 → task-N-brief.md(含 Global Constraints)
  python workspace_tools.py snap <N> <file>... # 快照任务 N 将改动的文件 → snapshots/taskN/
  python workspace_tools.py diff <N>          # difflib 生成 task-N-diff.patch(快照 vs 当前工作区)
"""
import difflib
import os
import re
import shutil
import sys

ROOT = r"D:\personal\InkCube"
WS = os.path.join(ROOT, ".superpowers", "sdd", "2026-10-09-phase1-knowledge-first-pulsebar")
PLAN = os.path.join(ROOT, "docs", "superpowers", "plans",
                    "2026-10-09-phase1-knowledge-first-pulsebar.md")


def norm(p):
    return p.replace("\\", "/")


def cmd_briefs():
    with open(PLAN, encoding="utf-8") as f:
        text = f.read()
    # 头部(Global Constraints 在头部里)
    head_end = text.index("### Task 1:")
    header = text[:head_end]
    m = re.search(r"## Global Constraints.*?(?=\n---\n)", header, re.S)
    constraints = m.group(0) if m else ""
    parts = re.split(r"(?=### Task \d+:)", text)
    tasks = [p for p in parts if p.startswith("### Task ")]
    for t in tasks:
        num = re.match(r"### Task (\d+):", t).group(1)
        # 去掉任务尾部的 --- 分隔线
        t = t.rstrip()
        if t.endswith("---"):
            t = t[:-3].rstrip()
        out = os.path.join(WS, f"task-{num}-brief.md")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# 任务简报(Task {num})——计划全文提取,含全局约束\n\n")
            f.write(constraints + "\n\n---\n\n" + t + "\n")
        print("WROTE", norm(out))


def cmd_snap(num, files):
    d = os.path.join(WS, "snapshots", f"task{num}")
    os.makedirs(d, exist_ok=True)
    for rel in files:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(d, rel.replace("/", "_"))
        shutil.copy2(src, dst)
        print("SNAP", rel)


def cmd_diff(num):
    d = os.path.join(WS, "snapshots", f"task{num}")
    if not os.path.isdir(d):
        print("NO_SNAPSHOT_DIR", d)
        return
    out_path = os.path.join(WS, f"task-{num}-diff.patch")
    lines_out = []
    for snap in sorted(os.listdir(d)):
        rel = snap.replace("_", "/")
        cur = os.path.join(ROOT, rel)
        with open(os.path.join(d, snap), encoding="utf-8", errors="replace") as f:
            old = f.readlines()
        if os.path.isfile(cur):
            with open(cur, encoding="utf-8", errors="replace") as f:
                new = f.readlines()
        else:
            new = []
        if old != new:
            lines_out.append(f"===== {rel} =====\n")
            lines_out.extend(difflib.unified_diff(
                old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=6))
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines_out) if lines_out else f.write("(no changes)\n")
    print("WROTE", norm(out_path), "changed_files:",
          sum(1 for l in lines_out if l.startswith("=====")))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "briefs":
        cmd_briefs()
    elif sys.argv[1] == "snap":
        cmd_snap(sys.argv[2], sys.argv[3:])
    elif sys.argv[1] == "diff":
        cmd_diff(sys.argv[2])
    else:
        print(__doc__)
