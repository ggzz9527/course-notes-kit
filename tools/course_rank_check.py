# -*- coding: utf-8 -*-
"""course_rank_check.py —— rank 的 kind 判据可分性检验（Fisher 比值）

用途：你换了课程/拍摄环境后，先确认「按 green 分板书/人物」这套判据还成立，
否则后面 rank 表的分类会整列错。

判据：对若干集逐帧算
  类间方差 = 板书/投影 组 mean(green) 与 人物/废帧 组的差
  类内方差 = 各组自己的方差
  Fisher   = 类间 / 类内     —— 越大越好分；<1 说明两组糊在一起，任何阈值都没用

依赖：同目录下已有 course_scan.py 产出的 frames_scan.csv（kind 列）
     和 course_run.py rank 产出的 frames_rank.csv（green 列）。

用法：
  py course_rank_check.py <媒体根目录> <集目录名> [集目录名 ...]

例：
  py course_rank_check.py tools/media/course BV1xx-ep01 BV1xx-ep02
"""
import csv
import io
import os
import sys

DEFAULT_BASE = os.path.join("tools", "media", "course")


def _find(d, name):
    for root, _dirs, fs in os.walk(d):
        if name in fs:
            return os.path.join(root, name)
    return None


def load(base, ep):
    d = os.path.join(base, ep)
    rp = _find(d, "frames_rank.csv")
    sp = _find(d, "frames_scan.csv")
    if not rp or not sp:
        return None, None
    rank = {"f_%04d" % int(r["t"]): float(r["green"])
            for r in csv.DictReader(io.open(rp, encoding="utf-8"))}
    # frames_scan.csv 是 utf-8-sig（course_scan 写的），但若被重存过可能没有 BOM。
    # 用 utf-8-sig 读无 BOM 文件也安全（BOM 可有可无），这里显式兼容两种列名。
    scan = {}
    for r in csv.DictReader(io.open(sp, encoding="utf-8-sig")):
        key = (r.get("frame") or "").replace(".png", "")
        scan[key] = r.get("kind") or ""
    return rank, scan


def mean(v):
    return sum(v) / len(v) if v else 0.0


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        print("缺参数：至少给一个集目录名（如 BV1xx-ep01）")
        return 2

    base = DEFAULT_BASE
    names = argv
    # 第一个参数若是个已存在的目录，就当媒体根目录
    if os.path.isdir(argv[0]):
        base = argv[0]
        names = argv[1:]
    if not names:
        print("缺集目录名（媒体根目录下的一级子目录，如 BV1xx-ep01）")
        return 2

    print(f"{'集':<22}{'板书组n':>8}{'mean':>7}{'人物组n':>8}{'mean':>7}"
          f"{'类间':>8}{'类内':>8}{'Fisher':>8}  结论")
    print("-" * 86)

    ok = 0
    for ep in names:
        rank, scan = load(base, ep)
        if not rank or not scan:
            print(f"{ep:<22}{'—— 缺 frames_rank.csv 或 frames_scan.csv，跳过'}")
            continue
        A, B = [], []
        for f, g in rank.items():
            k = scan.get(f)
            if k in ("板书", "投影"):
                A.append(g)
            elif k in ("人物", "废帧"):
                B.append(g)
        if not A or not B:
            print(f"{ep:<22}{'—— 某一组为空，跳过'}")
            continue
        mA, mB = mean(A), mean(B)
        within = (sum((x - mA) ** 2 for x in A) / len(A)
                  + sum((x - mB) ** 2 for x in B) / len(B)) / 2
        between = (mA - mB) ** 2
        fisher = between / within if within else 9e9
        verdict = "可分" if fisher >= 1.0 else "不可分（换判据）"
        print(f"{ep:<22}{len(A):>8}{mA:>7.1f}{len(B):>8}{mB:>7.1f}"
              f"{between:>8.1f}{within:>8.1f}{fisher:>8.2f}  {verdict}")
        ok += 1

    if not ok:
        print("\n没有任何一集算出结果：先跑 course_run.py rank / course_scan.py。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
