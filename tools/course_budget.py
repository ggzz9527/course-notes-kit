# -*- coding: utf-8 -*-
"""course_budget.py —— 写正文**前**先分配每节字数，避免写超再整段重写。

2026-11-23 ep16 复盘：那一集 K11 一口气写了 687 字，超单点上限 400+80，
只能把它劈成 K11/K12 重写；劈完还要同步改 frontmatter、重跑三遍 note_check。
根因不是写得多，是**动手前没配额**。

用法：
  # 1) 写之前：按知识点清单出配额表
  py tools/course_budget.py plan <集目录> K1_xxx K2_xxx ... [--kp 380] [--fig 3]

  # 2) 写之后：看看每节实际用了多少、离配额多远（不用等 note_check 全跑完）
  py tools/course_budget.py used <集目录>

口径与 note_check.py 一致（复用 course_verify 的算法，避免两套数）：
  字数预算 = knowledge_points × 400；合格带 = [0, 预算 + 800]
  单点正文 ≤ 400+40（超了 warn），> 400+80 直接 fail
  图后逐点解读 ≤ 120+20

配额怎么给（默认值，可按集调）：
  kp=380  —— 留 20 字余量给超一点的知识点，落到 400 以内
  fig=3   —— 每 3 个知识点配 1 张图（ep14/15/16 实测都落在这个区间）
  lead=150 开头速览、tail=200 闭环/自测
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import course_verify as cv          # noqa: E402


def _cjk(s):
    return len(re.findall(r"[\u4e00-\u9fff]", s))


def load(path):
    return io.open(path, encoding="utf-8").read()


def parse_kps(txt):
    """按 `### K<n>_xxx` 切出每节正文（到下一个 ### / ## 为止）"""
    lines = txt.splitlines()
    secs = []
    cur = None
    for s in lines:
        m = re.match(r"^###\s+(K\d+_[^\s（(]+)", s.strip())
        if m:
            if cur:
                secs.append(cur)
            cur = {"id": m.group(1), "buf": []}
            continue
        if cur is not None:
            if re.match(r"^#{2,3}\s", s):
                secs.append(cur)
                cur = None
                continue
            cur["buf"].append(s)
    if cur:
        secs.append(cur)
    out = []
    for c in secs:
        # 口径必须与 note_check 完全一致：直接复用 course_verify 的计数器
        # （它排除代码块/公式/表格/图注/逐点解读）。
        # 2026-11-23 我先自己写了个「去掉 > 开头的行」——同一份讲义它报 K6=509 字，
        # note_check 报最大 342，两个数打架。工具自己先有了口径 bug，比没有工具更坏。
        b, d, _cat = cv._count_body_cjk(c["buf"])
        out.append({"id": c["id"], "body": b, "duo": d})
    return out


def cmd_plan(a):
    n = len(a.kps)
    total = n * 400
    upper = total + a.tol
    print(f"# {n} 个知识点 ⇒ 字数预算 {total}，合格带 ≤{upper}")
    print()
    print(f"{'#':<3}{'知识点':<34}{'正文配额':>8}  说明")
    print("-" * 66)
    kp = a.kp
    fig_every = max(1, int(round(n / float(a.fig)))) if a.fig else 0
    for i, k in enumerate(a.kps, 1):
        tag = ""
        if fig_every and i % fig_every == 0:
            tag = "＋1 图（图注40 + 逐点解读110）"
        print(f"{i:<3}{k:<34}{kp:>8}  {tag}")
    body_budget = n * kp
    figs = a.fig
    fig_cost = figs * (40 + 110)
    print("-" * 66)
    print(f"{'':<3}{'小计':<34}{body_budget:>8}  ＋ 图 {figs} 张 ≈ {fig_cost}")
    est = a.lead + body_budget + fig_cost + a.tail
    print(f"\n预估总字数 ≈ {est}（开头 {a.lead} + 正文 {body_budget}"
          f" + 图 {fig_cost} + 收尾 {a.tail}）")
    if est > upper:
        print(f"⚠ 超出合格带上限 {upper}，需砍 {est - upper} 字："
              f"先把 kp 从 {kp} 降到 {max(200, kp - (est - upper) // n - 1)}")
    else:
        print(f"✅ 在合格带内，余量 {upper - est} 字")
    return 0


def cmd_used(a):
    p = os.path.join(a.ep_dir, "10-讲义.md")
    if not os.path.isfile(p):
        print(f"找不到 {p}")
        return 2
    txt = load(p)
    m = re.search(r"knowledge_points:\s*\[(.*?)\]", txt, re.S)
    kps = [x.strip() for x in m.group(1).split(",")] if m else []
    n = len(kps) or 1
    total = n * 400
    upper = total + a.tol
    secs = parse_kps(txt)
    print(f"# {n} 个知识点（frontmatter）／正文实际切出 {len(secs)} 节")
    print(f"# 预算 {total}，合格带 ≤{upper}")
    print()
    print(f"{'#':<3}{'知识点':<34}{'正文':>6}{'图解':>6}  {'判定'}")
    print("-" * 72)
    worst = 0
    for i, s in enumerate(secs, 1):
        b = s["body"]
        worst = max(worst, b)
        v = ("FAIL" if b > 480 else ("warn" if b > 440 else "ok"))
        print(f"{i:<3}{s['id']:<34}{b:>6}{s['duo']:>6}  {v}")
    print("-" * 72)
    s = cv.summary(load(p)) if hasattr(cv, "summary") else None
    tot = _cjk(re.sub(r"^>.*$", "", txt, flags=re.M))
    print(f"单点正文最大: {worst}（配额 400，warn 440 / fail 480）")
    print(f"全篇正文汉字（不含图注）: {tot} / ≤{upper}"
          f"  {'✅ 合格' if tot <= upper else '❌ 超 ' + str(tot - upper)}")
    if worst > 480:
        print(f"\n⚠ 有单点超 480，建议按下面拆：")
        for i, s in enumerate(secs, 1):
            if s["body"] > 480:
                print(f"   {s['id']}  {s['body']} 字 → 拆成两节，"
                      f"每节约 {s['body'] // 2} 字")
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="course_budget.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("plan", help="写之前：出每节配额")
    pp.add_argument("ep_dir")
    pp.add_argument("kps", nargs="+")
    pp.add_argument("--kp", type=int, default=380)
    pp.add_argument("--fig", type=int, default=3, help="每几个知识点配 1 张图")
    pp.add_argument("--lead", type=int, default=150)
    pp.add_argument("--tail", type=int, default=200)
    pp.add_argument("--tol", type=int, default=800)
    pp.set_defaults(func=cmd_plan)

    pu = sub.add_parser("used", help="写之后：每节实际用量")
    pu.add_argument("ep_dir")
    pu.add_argument("--tol", type=int, default=800)
    pu.set_defaults(func=cmd_used)

    a = ap.parse_args()
    sys.exit(a.func(a))


if __name__ == "__main__":
    main()
