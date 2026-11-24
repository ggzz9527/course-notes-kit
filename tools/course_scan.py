#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""course_scan.py —— 视觉子代理批量判帧的**本地两段**：出清单 / 并结果

背景（2026-11-23 ep14 全量基线）：
  几百帧交给视觉子代理（provider 由你用哪个就用哪个）逐帧判「板书/投影/废帧」，
  拿到的是**段落边界**，不是帧内容。实测铁律两条：
    1. `text` 一律丢弃——139 帧里它抄回来的几乎全是同一句黑板边角料，
       是"假稠密表"，比 null 更危险（null 会重读，假稠密不会）；
    2. `kind` 要做 3 帧多数票平滑——单帧翻转是画面过渡抖动。

为什么不在脚本里直接打 API：
  沙箱内直连 dashscope 实测返回
  `<400> InternalError.Algo: Temporary multimodal download failure: TimeoutError`
  （base64 data-URI 与域名都换过，两个门牌都报同一句）。所以读图仍由会话里的
  `workflow` 起子代理跑，本脚本只做「出清单 → 并结果」这两件**纯本地**的事。

用法：
  # 1) 出待判清单（模型去跑这个清单）
  py tools/course_scan.py list <媒体目录> [--every 10] [--batch 6] [--json]

  # 2) 子代理结果落盘后并表（JSON 数组：[{f, r}] 或 {帧号: "..."}）
  py tools/course_scan.py merge <媒体目录> <结果.json> [--smooth 1]
  → 写 frames_scan.csv + 打印段落表

结果 JSON 的三种可接受形态：
  a) [{"f":"f_0060","r":"{\\"kind\\":\\"板书\\",...}"}]      ← workflow 直接返回的
  b) {"f_0060": "{\\"kind\\":...}", ...}
  c) {"f_0060": {"kind":"板书","occluded":"轻"}, ...}       ← 已解析过的

退出码：0 成功；1 失败；2 用法错。
"""
import argparse
import collections
import csv
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

PROMPT = """用 read_image 读 {path} 。只写你真正看见的，看不清写[模糊]，绝不许根据课程内容猜测或补全。

按这个格式回纯 JSON（不要代码块、不要解释、不要任何多余文字）：
{{"kind":"板书|投影|远景|人物|废帧","occluded":"无|轻|重","text":"只抄最显眼那一行，20字以内"}}

kind 判定标准（只按"画面里板面占多大、能不能直接读数"分，不要按内容猜）：
  板书 = 黑板/白板占画面主体，字能直接读清
  投影 = 屏幕/投影幕占画面主体，字能直接读清
  远景 = 教室全景，黑板或投影幕在画面里但只占一小块，字偏小需要裁剪放大才能读
  人物 = 镜头对着老师或学生，画面里没有可读板面
  废帧 = 黑场、台标、转场、纯墙壁

注意：远景 和 人物 都**不是**废帧。远景说明那一时段有内容，只是要裁剪；
把它填成废帧会让后面的挑帧漏掉整段。"""

KINDS = ("板书", "投影", "远景", "人物", "废帧")
ORDER = {k: i for i, k in enumerate(KINDS)}


def log(msg, err=False):
    """`err=True` 走 stderr。`--json` 模式下必须走 stderr，否则日志行混进
    stdout，`> x.json` 出来的文件首行是 `[scan] 299 帧…`，整份 JSON 报废
    （2026-11-24 ep18 踩过）。"""
    print(f"[scan] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def frame_list(media_dir, every=10):
    fdir = os.path.join(media_dir, "frames")
    if not os.path.isdir(fdir):
        raise SystemExit(f"找不到 {fdir}")
    out = []
    for name in sorted(os.listdir(fdir)):
        if not (name.startswith("f_") and name.endswith(".png")):
            continue
        try:
            t = int(name[2:-4])
        except ValueError:
            continue
        if every and t % every:
            continue
        out.append((t, name, os.path.join(fdir, name)))
    return out


def cmd_list(a):
    frames = frame_list(a.media_dir, a.every)
    if not frames:
        log("一帧都没有，先跑 course_run.py fetch")
        return 1
    nbatch = (len(frames) + a.batch - 1) // a.batch
    log(f"{len(frames)} 帧，每批 {a.batch} 个 ⇒ {nbatch} 批", err=a.json)
    if a.json:
        # 给 workflow 直接吃的清单
        payload = {
            "dir": os.path.join(os.path.abspath(a.media_dir), "frames") + os.sep,
            "batch": a.batch,
            "frames": [f[1] for f in frames],
            "prompt": PROMPT,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    print()
    print("=" * 72)
    print("提示词（逐帧替换 {path}）：")
    print("=" * 72)
    print(PROMPT.format(path="<媒体目录>/frames/f_0060.png"))
    print()
    print("=" * 72)
    print(f"帧清单（{len(frames)} 个，建议每批 {a.batch} 个并发）：")
    print("=" * 72)
    for i, (t, name, _p) in enumerate(frames):
        if i % a.batch == 0:
            print(f"\n--- 批 {i // a.batch + 1} ---")
        print(name)
    print()
    log("跑完把结果存成 JSON，再 `course_scan.py merge <媒体目录> <结果.json>`")
    return 0


def _parse_one(v):
    """一条结果 → dict(kind, occluded, text)；解析不出来返回 None"""
    if v is None:
        return None
    if isinstance(v, dict):
        d = v
    else:
        s = str(v).strip()
        s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
        d = None
        try:
            d = json.loads(s)
        except Exception:
            # 容错：正则抠字段（实测有帧的 bbox 多一个引号、或夹了说明文字）
            m = re.search(r'"kind"\s*:\s*"([^"]+)"', s)
            if not m:
                return None
            d = {"kind": m.group(1)}
            mo = re.search(r'"occluded"\s*:\s*"([^"]+)"', s)
            if mo:
                d["occluded"] = mo.group(1)
            mt = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', s)
            if mt:
                d["text"] = mt.group(1)
    kind = str(d.get("kind", "")).strip()
    if kind not in KINDS:
        return None          # 「没有 kind 字段」＝失败，不当"这帧没内容"
    return {"kind": kind,
            "occluded": str(d.get("occluded", "")).strip(),
            "text": str(d.get("text", "")).replace("\n", " / ").strip()}


# 同一组内才允许互相平滑。跨组不平滑——因为跨组翻转往往是真的镜头切换，
# 抹平它会把一整个段落吞掉（实测 ep15 收尾 5 帧远景夹在板书中间，
# 无保护时会被整段抹成「板书」，那 44:10 之后就没有可取帧了）。
GROUP = {
    "板书": "直读", "投影": "直读",   # 占画面主体，字能直接读
    "远景": "裁剪",                    # 有内容但字小，要裁
    "人物": "空", "废帧": "空",        # 无可读板面
}


def _smooth(seq, radius=1):
    """3 帧多数票：只在窗口凑够 3 个有效值、且同组邻居一致时才改（保守）

    两道护栏，都是回测踩出来的：
      1. 窗口不足 3 个有效值时**不动**——2 个值里"多数票"等于"另一个"，
         会把孤立的正确判读改成错的（ep14 的 f_0010 就这样被误改）；
      2. 只跟**同一大类**的邻居投票（见 GROUP）——跨类投票会把真的镜头
         切换当抖动抹掉，整段丢失。
    """
    out = list(seq)
    n = len(out)
    for i in range(n):
        if out[i] is None:
            continue
        g = GROUP.get(out[i])
        lo, hi = max(0, i - radius), min(n, i + radius + 1)
        win = [x for x in out[lo:hi] if x and GROUP.get(x) == g]
        if len(win) < 3:
            continue
        c = collections.Counter(win)
        top, cnt = c.most_common(1)[0]
        if cnt >= 2 and out[i] != top:
            out[i] = top
    return out


def _base(name):
    """'f_0060.png' / 'f_0060' → 'f_0060'"""
    s = str(name).strip()
    return s[:-4] if s.lower().endswith(".png") else s


def _expand_workflow(raw):
    """workflow 返回值 → {帧stem: 结果}。

    `workflow` 里 `agent()` 拿到的是**一批帧的整段回复**（一个 JSON 数组），
    不是逐帧结果，所以它的返回长这样：
        [{"batch":1,"frames":[...],"raw":"[{\\"f\\":...}, ...]"}, ...]

    2026-11-23 ep16 的教训：当时没写这一层，我**手抄了 283 帧的分类**去凑
    merge 的入参——既烧 token 又埋错字风险（错一个字就整段丢失，且不报错）。
    拆批也交给脚本，手抄这一步就不存在了。
    """
    pairs, salvaged = {}, 0
    for it in raw:
        if not isinstance(it, dict):
            continue
        blob = it.get("raw", it.get("r"))
        if blob is None:
            continue
        if isinstance(blob, str):
            s = re.sub(r"^```(?:json)?|```$", "", blob.strip(), flags=re.M).strip()
            try:
                items = json.loads(s)
            except Exception:
                # 整批坏了：退回逐条正则，能捞几个算几个
                for m in re.finditer(
                        r'"f"\s*:\s*"([^"]+)"[^{}]*?"kind"\s*:\s*"([^"]+)"'
                        r'(?:[^{}]*?"occluded"\s*:\s*"([^"]*)")?', s):
                    pairs[_base(m.group(1))] = {
                        "kind": m.group(2), "occluded": m.group(3) or "",
                        "text": ""}
                    salvaged += 1
                continue
        else:
            items = blob
        if not isinstance(items, list):
            items = [items]
        for cell in items:
            if isinstance(cell, dict) and "f" in cell:
                pairs[_base(cell["f"])] = {
                    k: cell.get(k, "") for k in ("kind", "occluded", "text")}
    if salvaged:
        log(f"⚠ 有 {salvaged} 条靠正则从坏 JSON 里捞出，建议抽查")
    return pairs


def cmd_merge(a):
    frames = frame_list(a.media_dir, a.every)
    if not frames:
        log("一帧都没有")
        return 1
    raw = json.load(io.open(a.result, encoding="utf-8"))
    if isinstance(raw, list):
        if raw and isinstance(raw[0], dict) and "raw" in raw[0]:
            # workflow 形态：一批一段 raw 字符串
            pairs = _expand_workflow(raw)
            log(f"workflow 形态：{len(raw)} 批 → {len(pairs)} 帧")
        else:
            pairs = {}
            for it in raw:
                if isinstance(it, dict) and "f" in it:
                    pairs[_base(it["f"])] = it.get("r")
                elif isinstance(it, dict) and "frame" in it:
                    pairs[_base(it["frame"])] = it.get("raw", it.get("r"))
    elif isinstance(raw, dict):
        pairs = raw
    else:
        log("结果 JSON 既不是数组也不是对象")
        return 2

    rows, failed = [], []
    for t, name, _p in frames:
        stem = name[:-4]
        p = _parse_one(pairs.get(stem, pairs.get(name)))
        if p is None:
            failed.append(stem)
            rows.append({"t": t, "frame": name, "kind": "", "occluded": "",
                         "text": "", "status": "FAIL"})
        else:
            rows.append({"t": t, "frame": name, "kind": p["kind"],
                         "occluded": p["occluded"], "text": p["text"],
                         "status": "OK"})

    if a.smooth:
        # 只平滑「有结果」的帧；失败帧的 kind 留空，别用邻居填空——
        # 那会把"没读到"伪装成"读到了"，比不填更糟。
        base = [r["kind"] if r["status"] == "OK" else None for r in rows]
        fixed = _smooth(base, 1)
        changed = [(rows[i]["frame"], rows[i]["kind"], fixed[i])
                   for i in range(len(rows))
                   if rows[i]["status"] == "OK" and rows[i]["kind"] != fixed[i]]
        for i, r in enumerate(rows):
            r["kind_raw"] = r["kind"]
            if r["status"] == "OK":
                r["kind"] = fixed[i]
        if changed:
            log(f"平滑改了 {len(changed)} 帧：" +
                ", ".join(f"{f}({a_}→{b_})" for f, a_, b_ in changed[:12]))

    out = os.path.join(os.path.abspath(a.media_dir), "frames_scan.csv")
    with io.open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["t", "frame", "kind", "kind_raw",
                                           "occluded", "text", "status"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---- 段落表
    segs, cur = [], None
    for r in rows:
        k = r["kind"] or "待人工"
        if cur and cur["kind"] == k:
            cur["t1"] = r["t"]
            cur["n"] += 1
        else:
            if cur:
                segs.append(cur)
            cur = {"kind": k, "t0": r["t"], "t1": r["t"], "n": 1}
    if cur:
        segs.append(cur)

    def mmss(s):
        return f"{int(s)//60:02d}:{int(s)%60:02d}"

    print()
    print(f"{'段':<4}{'起':>8}{'止':>8}{'帧':>5}  类型")
    print("-" * 40)
    for i, s in enumerate(segs, 1):
        print(f"{i:<4}{mmss(s['t0']):>8}{mmss(s['t1']):>8}{s['n']:>5}  {s['kind']}")

    c = collections.Counter(r["kind"] or "待人工" for r in rows)
    print()
    log(f"写出 {out}")
    log(f"分布 {dict(c)}")
    if failed:
        # 失败帧不参与平滑、不留空 kind——「没读到」必须显式暴露成待人工，
        # 不许用邻居的类别填空（那等于把故障伪装成结论）。
        log(f"⚠ {len(failed)} 帧没有结果，必须主模型亲自看：")
        for r in rows:
            if r["status"] == "FAIL":
                log(f"    {r['frame']}  {mmss(r['t'])}")
    if c.get("废帧", 0) > len(rows) * 0.5:
        log("⚠ 废帧过半，分类可能整体失效——回退主模型抽帧看")
    # 排版型提示：板书占比过低
    if c.get("板书", 0) < len(rows) * 0.05:
        log("⚠ 板书 < 5%：若是 blackboard/mixed 型课程，说明分类器没认出黑板")
    # 一条有效结果都没并上 = 整批失败，返回 1。只打印警告会让只看退出码的
    # 自动化把「没并上任何结果」当成成功（2026-11-24 排查时复现）。
    ok_n = sum(1 for r in rows if r["status"] == "OK")
    if ok_n == 0:
        log(f"整批 {len(rows)} 帧没有一条有效结果，退出码 1（请先修好子代理输出再重跑）")
        return 1
    return 0


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="course_scan.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="出待判帧清单 + 提示词")
    pl.add_argument("media_dir")
    pl.add_argument("--every", type=int, default=10)
    pl.add_argument("--batch", type=int, default=6,
                    help="每批并发数（workflow 活跃子代理上限 8，别超 6）")
    pl.add_argument("--json", action="store_true", help="机器可读，给 workflow 吃")
    pl.set_defaults(func=cmd_list)

    pm = sub.add_parser("merge", help="子代理结果 → frames_scan.csv + 段落表")
    pm.add_argument("media_dir")
    pm.add_argument("result")
    pm.add_argument("--every", type=int, default=10)
    pm.add_argument("--smooth", type=int, default=1,
                    help="kind 多数票平滑半径（默认 1＝3 帧），0 关闭")
    pm.set_defaults(func=cmd_merge)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
