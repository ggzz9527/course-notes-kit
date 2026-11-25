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
    """切出每节正文。切分规则与 `course_verify.cmd_wc` **完全一致**（别另写一套）。

    三处形态都要认（历史上真的都出现过）：
        ### K1 · 名称        ep03 起的主流写法
        ## 1. K1 · 名称      ep02（K 挂在二级标题上）
        ### 4.1 子点         不带 K，留在当前段里

    2026-11-25 修：以前只认 `### K1_xxx`（下划线形态），于是本命令对
    **几乎所有集都报"正文实际切出 0 节"**——工具静默给全 0、看起来像"没超"，
    比报错更危险。修完又发现 ep02 的 `## 1. K1 ·` 仍漏，一并补上。
    """
    lines = txt.splitlines()
    secs = []
    cur = None
    for raw in lines:
        t = raw.strip()
        if t.startswith('#') and re.match(r'^#{2,4}\s', t):
            kk = cv.kp_head(t)
            if kk is not None:
                cur_k = kk
                # 同一 K 的重复标题（子点标题里也写了 K 的少数情况）不新开段
                if not (cur and cur["id"] == "K%d" % cur_k):
                    if cur:
                        secs.append(cur)
                    cur = {"id": "K%d" % cur_k, "buf": []}
                continue
            # 无 K 标题：## 是段边界，### 是子点
            if re.match(r'^##\s', t):
                if cur:
                    secs.append(cur)
                    cur = None
                continue
            # ### 无 K ⇒ 子点，落到下面 append
        if cur is not None:
            cur["buf"].append(raw)
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
    # 分篇支持（2026-11-25）：多篇按 上→中→下 顺序拼接后统一统计
    import glob as _glob
    cand = sorted(_glob.glob(os.path.join(a.ep_dir, "10-讲义*.md")))
    if not cand:
        print(f"找不到 {a.ep_dir}\\10-讲义*.md")
        return 2
    if len(cand) > 1:
        print("# 分 %d 篇：%s" % (len(cand), "、".join(os.path.basename(x) for x in cand)))
    txt = "\n".join(load(x) for x in cand)
    # 知识点数取**各篇并集**（2026-11-25）：拼好的 txt 里 frontmatter 会出现两次，
    # 旧写法 `re.search` 只吃第一个，于是分篇时预算直接砍半（报 2 个点、实际 4 个）。
    _fm = cv.merged_lecture_fm(a.ep_dir)
    _raw = _fm.get("knowledge_points", "")
    kps = re.findall(r"K\d+_[^,\]\s]+", _raw)
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
