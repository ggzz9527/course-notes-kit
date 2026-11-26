# -*- coding: utf-8 -*-
"""course_gate.py —— 拉课开工前/挑帧阶段的机器闸门

ep23 复盘（2026-11-24）把 skill ②-C 的"先量标尺""brief 时刻对齐""读图预算"
从靠自觉改成靠命令。三个子命令，都不烧 LLM：

  py course_gate.py times   <媒体目录> <时刻列表>       # 对齐 frames.csv，剔掉越界时刻
  py course_gate.py ruler   <集目录>                    # 查知识点卡里有没有量过标尺
  py course_gate.py budget  <集目录> <读图张数>          # 按歧义点数核读图预算
  py course_gate.py report  <集目录>                    # 一次跑全部检查

times 的入参接受 `450,540` 或 `07:30,09:00`；输出可直接喂给 course_run.py brief。
退出码：0 全过；1 有闸门未过（打印哪一条、怎么修）。
"""
import argparse
import csv
import io
import os
import re
import sys

# Windows 控制台默认 GBK，遇到 → ⇒ ≤ 这类符号会 UnicodeEncodeError 崩掉。
# 这里强制 stdout/stderr 走 UTF-8，脚本里就可以放心用符号。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FRAME_RE = re.compile(r"f_(\d+)\.png$")


def _frames(media_dir):
    """返回 [(秒, 文件名)]，按秒排序。"""
    d = os.path.join(media_dir, "frames")
    if not os.path.isdir(d):
        return []
    out = []
    for f in os.listdir(d):
        m = FRAME_RE.search(f)
        if m:
            out.append((int(m.group(1)), f))
    return sorted(out)


def _last_t(media_dir):
    fs = _frames(media_dir)
    return fs[-1][0] if fs else None


class TimeSpecError(ValueError):
    pass


def parse_times(spec):
    """把 `450,540` 或 `07:30,1:02:03` 解析成秒列表。

    容错要点（B3）：空串、负数、非数字都抛 TimeSpecError 给可读提示，
    不让 argparse 那种 "the following arguments are required" 糊过去。
    """
    if spec is None or not str(spec).strip():
        raise TimeSpecError("时刻表是空的。至少给一个时刻，例如 \"220,900\" 或 \"07:30\"")

    out = []
    for tok in re.split(r"[,\s]+", str(spec).strip()):
        if not tok:
            continue
        try:
            if ":" in tok:
                parts = [int(x) for x in tok.split(":")]
                if len(parts) > 3:
                    raise TimeSpecError("时刻 %r 段数过多（最多 时:分:秒）" % tok)
                sec = 0
                for q in parts:
                    sec = sec * 60 + q
            else:
                sec = int(tok)
        except ValueError:
            raise TimeSpecError("时刻 %r 不是数字，也不是 分:秒 格式" % tok)
        if sec < 0:
            raise TimeSpecError("时刻 %d 是负数，时间不能为负" % sec)
        out.append(sec if ":" in tok else sec)
    if not out:
        raise TimeSpecError("时刻表里没有可用数字：%r" % spec)
    return out


# ---------------------------------------------------------------- times
def cmd_times(a):
    fs = _frames(a.media_dir)
    if not fs:
        # 2026-11-26 真课自检改：原来一律说「没有 frames/」，而旧素材其实有
        # frames/、只是按帧序号命名（f0001.jpg）。帧序号不是秒，本工具按
        # f_<秒>.png 解析，读不了它——报「没有 frames/」会让人去重下素材，白跑。
        frames_dir = os.path.join(a.media_dir, "frames")
        if os.path.isdir(frames_dir) and os.listdir(frames_dir):
            print("[gate] %s/frames 里有文件，但没有 f_<秒>.png：本工具按「帧名=秒」解析。" % a.media_dir)
            print("       旧素材按帧序号命名（f0001.jpg），序号不是秒，读不了；")
            print("       修法：用 course_prep.py 按时间点重抽（新布局），或继续用旧流程手工挑帧。")
        else:
            print("[gate] %s 下没有 frames/，先跑 course_run.py fetch" % a.media_dir)
        return 1
    last = fs[-1][0]
    have = set(t for t, _ in fs)
    try:
        want = parse_times(a.times)
    except TimeSpecError as e:
        print("[gate] 时刻表有误：%s" % e)
        return 1

    ok, bad = [], []
    for t in want:
        if t > last:
            bad.append((t, "超出片长（最大帧 %d s）" % last))
            continue
        # 就近取实际存在的帧（步长可能不是 10 秒）
        if t in have:
            ok.append(t)
        else:
            near = min(fs, key=lambda x: abs(x[0] - t))
            if abs(near[0] - t) <= 10:
                ok.append(near[0])
            else:
                bad.append((t, "附近没有帧，最近的是 %d s" % near[0]))

    print("[gate] 帧范围 0–%d s（共 %d 张）" % (last, len(fs)))
    for t, why in bad:
        print("  [FAIL] %d s —— %s" % (t, why))
    ok = sorted(set(ok))
    print("  可用时刻：%s" % ",".join(str(t) for t in ok))
    print("  → 派活用：--times %s" % ",".join(str(t) for t in ok))
    return 0 if not bad else 1


# ---------------------------------------------------------------- ruler
RULER_HINT = "挑帧记录"
RECT_RE = re.compile(r"x1\s*,\s*y1\s*,\s*x2\s*,\s*y2\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def cmd_ruler(a):
    kp = os.path.join(a.ep_dir, "00-知识点.md")
    if not os.path.isfile(kp):
        print("[gate] 没找到 %s —— 先出知识点卡" % kp)
        return 1
    s = io.open(kp, encoding="utf-8").read()
    if RULER_HINT not in s:
        print("[gate] G1 未过：00-知识点.md 里没有「## 挑帧记录」一节")
        print("       （分篇时「挑帧记录」也写在 00-知识点.md 里，不要拆）")
        print("       修法：读一张整帧量出板面矩形，按下面格式记进去")
        print("       ## 挑帧记录")
        print("       - 板面矩形（取自 f_XXXX）：x1,y1,x2,y2 = 70,50,1120,550")
        return 1
    rects = RECT_RE.findall(s)
    if not rects:
        print("[gate] G1 未过：有「挑帧记录」但没写矩形（x1,y1,x2,y2 = ...）")
        return 1
    print("[gate] G1 通过：已量到 %d 组板面矩形" % len(rects))
    for r in rects:
        print("       x1,y1,x2,y2 = %s" % ",".join(r))
    return 0


# ---------------------------------------------------------------- G2
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
LEC_RANK = {"": 0, "-上": 1, "-中": 2, "-下": 3}


def _lecture_files(ep_dir):
    """集目录下全部讲义篇，按 上→中→下 排序（单篇时长度 1）。"""
    if not os.path.isdir(ep_dir):
        return []
    got = []
    for f in os.listdir(ep_dir):
        m = re.match(r"^10-讲义(.*?)\.md$", f)
        if m:
            got.append((LEC_RANK.get(m.group(1), 9), os.path.join(ep_dir, f)))
    got.sort()
    return [p for _r, p in got]


def _declared_media(ep_dir):
    """从集目录的 md 里读出已声明的媒体路径：tools/media/course/<BV>-epNN[/pNN]。

    这是**首选**依据——讲义/知识点卡里写过哪个 BV，就用哪个，
    避免"按 epNN 后缀猜"撞到同集的另一门课（R5：BV1xx411c7mD-ep23 vs BV1yy411c7mD-ep23）。
    """
    hits = set()
    # 讲义可能分篇（10-讲义-上/下.md），故按通配扫；2026-11-25 前只认单篇。
    # 2026-11-26 真课自检加：根目录**所有** .md 都扫。
    # 起因：另一门课三集的媒体路径只写在 `素材清单.md` 里，原来只认 10-讲义*/00-知识点/90-复查，
    # 于是 rects 报「按集号找不到媒体目录」——声明明明在，工具自己没看。
    lec = []
    if os.path.isdir(ep_dir):
        lec = [os.path.join(ep_dir, x) for x in sorted(os.listdir(ep_dir))
               if x.endswith(".md") and os.path.isfile(os.path.join(ep_dir, x))]
    for fp in lec + [os.path.join(ep_dir, "90-复查")]:
        files = []
        if os.path.isdir(fp):
            files = [os.path.join(fp, x) for x in os.listdir(fp) if x.endswith(".md")]
        elif os.path.isfile(fp):
            files = [fp]
        for f in files:
            try:
                txt = io.open(f, encoding="utf-8").read()
            except Exception:
                continue
            for m in re.finditer(
                    r"tools[\\/]media[\\/]course[\\/](BV[0-9A-Za-z]{10}-ep\d+)[\\/]?(p\d+)?",
                    txt):
                hits.add((m.group(1), m.group(2) or ""))
    return sorted(hits)


def _find_media_dir(ep_dir):
    """定位该集的媒体目录。优先看 md 里声明的路径，其次按集号反查并消歧。"""
    # 1) 环境变量最高优先
    env = os.environ.get("COURSE_MEDIA_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "media", "course")
    if not os.path.isdir(root):
        root = os.path.join("tools", "media", "course")

    # 2) md 里声明过的
    for bvep, sub in _declared_media(ep_dir):
        cand = os.path.join(root, bvep)
        if sub:
            p2 = os.path.join(cand, sub)
            if os.path.isdir(p2):
                return p2
        if os.path.isdir(cand):
            subs = [d for d in sorted(os.listdir(cand))
                    if os.path.isdir(os.path.join(cand, d))]
            return os.path.join(cand, subs[0]) if subs else cand

    # 3) 按集号反查，多候选时**不猜**
    m = re.search(r"(?:^|[\\/])ep(\d+)", os.path.abspath(ep_dir), re.I)
    if not m:
        return None
    n = int(m.group(1))
    cands = []
    if os.path.isdir(root):
        for d in sorted(os.listdir(root)):
            if re.search(r"-ep%d$" % n, d, re.I):
                cands.append(d)
    if len(cands) == 1:
        base = os.path.join(root, cands[0])
        subs = [x for x in sorted(os.listdir(base))
                if os.path.isdir(os.path.join(base, x))]
        return os.path.join(base, subs[0]) if subs else base
    if len(cands) > 1:
        print("[gate] 集号 ep%d 对应多个媒体目录，无法确定是哪个：" % n)
        for c in cands:
            print("         %s" % c)
        print("       修法：用 --shots-log 或 COURSE_MEDIA_DIR 显式指定")
        return "AMBIGUOUS"
    return None


def _read_shots_log(ep_dir):
    """找到 shots.log 并解析。返回 [(t, box, name, file, stamp)] 或 None。"""
    med = _find_media_dir(ep_dir)
    if not med:
        return None
    if med == "AMBIGUOUS":
        return "AMBIGUOUS"
    lp = os.path.join(med, "shots.log")
    if not os.path.isfile(lp):
        return "EMPTY_DIR:" + med
    return _parse_shots_log(lp)


def _parse_shots_log(lp):
    out = []
    for line in io.open(lp, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            t = int(parts[0])
        except ValueError:
            continue
        box = []
        if parts[1].strip():
            try:
                box = [int(v) for v in parts[1].split(",")]
            except ValueError:
                box = []
        out.append((t, box, parts[2] if len(parts) > 2 else "",
                    parts[3] if len(parts) > 3 else "",
                    parts[4] if len(parts) > 4 else ""))
    return out


def cmd_rects(a):
    """G2：同一时间点 ±NEAR 秒内、或同一块矩形被裁两次 → FAIL。

    这是 ep23 的实际病灶：t2390/t2360、t2600/t2200、t2050 两次……共重裁 6 次，
    每次都多一次 read_image 的重放成本。
    """
    near = a.near
    _ = near  # 保留给文档一致性
    rows = None
    if a.shots_log:
        if not os.path.isfile(a.shots_log):
            print("[gate] 指定了 --shots-log 但文件不存在：%s" % a.shots_log)
            return 1
        rows = _parse_shots_log(a.shots_log)
    else:
        rows = _read_shots_log(a.ep_dir)

    if rows is None:
        print("[gate] G2 无法判定：按集号找不到媒体目录")
        print("       修法：用 --shots-log <媒体目录>/shots.log 显式指定，")
        print("             或设 COURSE_MEDIA_DIR=<媒体目录>")
        return 1
    if rows == "AMBIGUOUS":
        return 1
    if isinstance(rows, str) and rows.startswith("EMPTY_DIR:"):
        print("[gate] G2 通过：媒体目录在（%s），但还没有 shots.log"
              % os.path.relpath(rows[10:]))
        print("       （本次是第一次裁图，或 shots 是本轮之前跑的；不算问题）")
        return 0

    if not rows:
        print("[gate] G2 通过：shots.log 为空（还没裁过图）")
        return 0

    print("[gate] G2: shots.log 里 %d 条裁切记录" % len(rows))

    # 判据 1：时间邻近
    ts = sorted(r[0] for r in rows)
    close = []
    for i in range(len(ts) - 1):
        if ts[i + 1] - ts[i] <= near:
            close.append((ts[i], ts[i + 1]))

    # 判据 2：同一矩形重复（完全相同 = 明显重裁同一块）
    boxes = {}
    for t, box, name, fn, stamp in rows:
        if box:
            key = tuple(box)
            boxes.setdefault(key, []).append((t, name))

    dup_box = {k: v for k, v in boxes.items() if len(v) > 1}

    # R1：主判据改成"时间邻近"，因为重裁时坐标总是会被改，靠 box 相同抓不住。
    # ep23 的六次重裁里，box 全都不同（t2050 两次都改了框），只有时间挨得很近。
    #
    # tight=10 是用 ep23 真值校准的：
    #   10s → 抓出 2 对真重裁（t2050 两次、t2390↔t2400），0 误报
    #   30s → 抓出 4 对，多出的 t2620↔t2600、t2390↔t2360 是不同板面（误报）
    # 别往大调：宁漏不误报，误报会让人习惯性忽略这条闸门。
    tight = a.tight          # 默认 10s（ep23 真值校准；30s 会多出跨板面的误报）
    near_pair = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            t1, b1, n1 = rows[i][0], rows[i][1], rows[i][2]
            t2, b2, n2 = rows[j][0], rows[j][1], rows[j][2]
            dt = abs(t1 - t2)
            if dt <= tight:
                near_pair.append((t1, n1, t2, n2, dt, b1 == b2))
    near_pair.sort(key=lambda x: x[4])

    rc = 0
    if near_pair:
        print("  [FAIL] 有 %d 对裁切时刻相差 ≤%ds —— 这是重裁，不是挑帧：" % (len(near_pair), tight))
        for t1, n1, t2, n2, dt, same in near_pair[:8]:
            tag = "同一矩形" if same else "改了坐标"
            print("         %5ds %-22s ↔ %5ds %-22s  差%2ds  %s"
                  % (t1, n1[:22], t2, n2[:22], dt, tag))
        print("         修法：下次先把板面矩形量进「## 挑帧记录」，一次裁对。")
        rc = 1

    # 完全相同 box 出现在 tight 之外，也算可疑（同一块板面被重新裁了一次）
    far_dup = {}
    for k, v in dup_box.items():
        ts2 = [t for t, _ in v]
        if max(ts2) - min(ts2) > tight:
            far_dup[k] = v
    if far_dup:
        print("  [WARN] 同一矩形在更远的时刻被重复裁切（可能是同结构的不同板面）：")
        for k, v in list(far_dup.items())[:4]:
            print("         box=%s → %s" % (",".join(str(x) for x in k),
                                           ", ".join("%ds" % t for t, _ in v)))

    if rc == 0:
        print("  [OK] 无重裁（%d 条记录，无 ≤%ds 的重复时刻）" % (len(rows), tight))
    return rc


# ---------------------------------------------------------------- budget
AMBIG_RE = re.compile(r"歧义点\s*[:：]\s*(\d+)")


def cmd_budget(a):
    kp = os.path.join(a.ep_dir, "00-知识点.md")
    src = io.open(kp, encoding="utf-8").read() if os.path.isfile(kp) else ""
    m = AMBIG_RE.search(src)
    if not m:
        print("[gate] G3 无法判定：00-知识点.md 里没写「歧义点：N」")
        print("       修法：在「挑帧记录」一节补一行 `**歧义点：2**`，下面逐条列为什么必须读图")
        print("       (判据：读图张数 = 会让讲义写错的歧义点数 + 1)")
        return 1
    amb = int(m.group(1))
    allow = amb + 1
    used = a.read_images
    if used is None:
        # B2：上一版这里 return 0，等于闸门可以被"不传参数"绕过。
        print("[gate] G3 未执行：缺 --read-images N")
        print("       这不是通过。请报本轮实际 read_image 次数；不报 = 没自查。")
        return 1
    print("[gate] G3：歧义点 %d ⇒ 允许读图 %d 张，实际 %d 张" % (amb, allow, used))
    if used > allow:
        print("  [FAIL] 超出 %d 张。超出的多半是「看清细节」，不算歧义点。" % (used - allow))
        print("       复查表里要写明超的那几张各读到了什么；没进正文的 = 白读。")
        return 1
    print("  [OK]")
    return 0


# ---------------------------------------------------------------- report
def cmd_report(a):
    rc = 0
    print("=== G1 标尺 ===")
    rc |= cmd_ruler(a)
    print("\n=== G2 二次裁切 ===")
    rc |= cmd_rects(a)

    print("\n=== G3 读图预算 ===")
    rc |= cmd_budget(a)
    print("\n=== G5 拆篇 ===")
    rc |= cmd_parts(a)

    print("\n=== 图块与 assets 对齐 ===")
    parts = _lecture_files(a.ep_dir)
    assets = os.path.join(a.ep_dir, "assets")
    if parts and os.path.isdir(assets):
        s = "\n".join(io.open(p, encoding="utf-8").read() for p in parts)
        used = set(re.findall(r"!\[[^\]]*\]\(assets/([^\)]+)\)", s))
        have = set(os.listdir(assets))
        orphan = sorted(have - used)
        missing = sorted(used - have)
        for f in missing:
            print("  [FAIL] 正文引用但文件缺失：%s" % f)
            rc |= 1
        for f in orphan:
            print("  [FAIL] assets 有但正文没引用：%s" % f)
            rc |= 1
        if not orphan and not missing:
            print("  [OK] %d 张图双向对齐" % len(have))
    else:
        print("  (跳过：还没有讲义或 assets)")
    return rc


# ---------------------------------------------------------------- G5 拆篇
KP_RE = re.compile(r"K(\d+)_")


def cmd_parts(a):
    """G5：知识点 ≥15 必须拆篇（T1/T2 闸门 2026-11-25 已满足，不再有借口）。"""
    ps = _lecture_files(a.ep_dir)
    if not ps:
        print("[gate] G5 跳过：还没有讲义")
        return 0
    # 知识点数取各篇 frontmatter 并集
    kps = set()
    for p in ps:
        t = io.open(p, encoding="utf-8").read()
        m = re.search(r"knowledge_points:\s*\[(.*?)\]", t, re.S)
        if m:
            kps |= set(KP_RE.findall(m.group(1)))
    # 正文实际出现的 K 段（防止 frontmatter 写多了）
    body_k = set()
    for p in ps:
        body_k |= set(re.findall(r"^#{2,4}\s*(?:\d+\s*[.、．]?\s*)?K(\d+)(?![0-9])",
                                 io.open(p, encoding="utf-8").read(), re.M))
    n = max(len(kps), len(body_k))
    print("[gate] G5：知识点 %d 个（frontmatter %d / 正文 %d），讲义 %d 篇"
          % (n, len(kps), len(body_k), len(ps)))
    if n >= 15 and len(ps) == 1:
        strict = getattr(a, "strict", False)
        tag = "FAIL" if strict else "WARN"
        print("  [%s] ≥15 个知识点却只有单篇 `%s`" % (tag, os.path.basename(ps[0])))
        print("         工具链闸门 T1/T2 已满足（2026-11-25），拆篇不再会丢集。")
        print("         修法：按主题拆成 `10-讲义-上.md` / `10-讲义-下.md`，")
        print("               各自一份 frontmatter，knowledge_points 只写本篇的。")
        print("         （拆篇只能拆在知识点之间，不许把一个知识点劈开）")
        if strict:
            return 1
        print("         ← 本次只是 WARN：历史已 reviewed 的集不追溯。")
        print("           新集交付时请加 --strict，让它变成 FAIL。")
        return 0
    if len(ps) > 1:
        print("  [OK] 已分 %d 篇：%s" % (len(ps),
                                        "、".join(os.path.basename(p) for p in ps)))
    else:
        print("  [OK] 单篇（<15 个知识点，不需要拆）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="拉课开工闸门（不烧 LLM）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("times", help="对齐 frames.csv，剔掉越界时刻")
    p.add_argument("media_dir")
    p.add_argument("times")
    p.set_defaults(func=cmd_times)

    p = sub.add_parser("ruler", help="查标尺是否已量")
    p.add_argument("ep_dir")
    p.set_defaults(func=cmd_ruler)

    p = sub.add_parser("budget", help="核读图预算")
    p.add_argument("ep_dir")
    p.add_argument("read_images", nargs="?", type=int, default=None,
                   help="本轮 read_image 次数；不给 = 未自查，判 FAIL")
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("rects", help="G2：查有没有二次裁切（读 shots.log）")
    p.add_argument("ep_dir")
    p.add_argument("--shots-log", default=None)
    p.add_argument("--near", type=int, default=60, help="（保留）宽松观察阈值")
    p.add_argument("--tight", type=int, default=10, help="重裁判定阈值（秒，默认 10；ep23 真值校准）")
    p.set_defaults(func=cmd_rects)

    p = sub.add_parser("parts", help="G5：≥15 个知识点必须拆篇")
    p.add_argument("ep_dir")
    p.add_argument("--strict", action="store_true",
                   help="把 WARN 升级为 FAIL（新集交付时用）")
    p.set_defaults(func=cmd_parts)

    p = sub.add_parser("report", help="一次跑全部检查")
    p.add_argument("ep_dir")
    p.add_argument("--read-images", type=int, default=None)
    p.add_argument("--shots-log", default=None)
    p.add_argument("--near", type=int, default=60)
    p.add_argument("--tight", type=int, default=10)
    p.add_argument("--strict", action="store_true",
                   help="G5 拆篇判据从 WARN 升级为 FAIL（新集交付时用）")
    p.set_defaults(func=cmd_report)

    a = ap.parse_args(_normalize_argv(sys.argv[1:]))
    sys.exit(a.func(a))


def _normalize_argv(argv):
    """让 `times <dir> "-5,100"` 这种负数/带横线的时刻不被 argparse 当成 option。

    argparse 会把以 - 开头的 token 当选项，于是 "-5,100" 直接报
    "the following arguments are required: times" 并 rc=2 —— 用户看到的是
    argparse 的糊话，不是"时间是负数"这个真问题（R3）。
    这里在 times 子命令的位置参数前插入 `--`，让后面全部按位置参数吃。
    """
    if not argv or argv[0] != "times":
        return argv
    # times 的签名：times media_dir times
    if len(argv) >= 3 and not argv[1].startswith("-"):
        return [argv[0], argv[1], "--"] + argv[2:]
    return argv


if __name__ == "__main__":
    main()
