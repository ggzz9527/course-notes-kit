#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""course_run.py —— 一次拉课的**全机械段**一条命令跑完

背景（2026-11-22）：ep08 那轮机械段被拆成 8 次 ops_bash + 7 次手动轮询转写，
重复劳动约 1.2M token。这里把「每次拉课都一模一样」的部分收成脚本：

    fetch   meta + 音频 + 转写 + 视频 + 抽帧 + 帧核验   ← 一条命令
    shots   把挑好的 frames/f_NNNN.png 裁切/放大成 assets/epNN-tNNNN-名字.png
    check   笔记自检（转调 note_check.py）+ 帧残差 + 素材清单草稿

用法：
  py tools/course_run.py fetch  BV1xx411c7mD 8
  py tools/course_run.py fetch  BV1xx411c7mD 8 --no-video        # 只要音频+转写
  py tools/course_run.py fetch  BV1xx411c7mD 8 --skip-existing    # 断点续跑（默认行为）
  py tools/course_run.py shots  <集目录> 2280:crop:60,55,660,425:1.6:输出特性与预夹断 \
                                        600:::刻度示例                      # t[:crop:x1,y1,x2,y2:scale:name]
                                        2690:proj:0:两级波特图投影           # 自动框投影幕
                                        2670:any:3:共集输出电阻              # 黑板优先，不像黑板再试投影
  py tools/course_run.py kind   <媒体目录> [--times 2440,2690]   # 裁图前先判帧类型：投影页/板书/存疑
  py tools/course_run.py check  <集目录> [--frames-csv ...]
  py tools/course_run.py rank   <媒体目录> [--every 10] [--top 10]
  py tools/course_run.py digest <媒体目录>

输出一律「一行一件事 + 最后的 MANIFEST 段」，方便一次读完不用第二次。
退出码：0 成功；1 有失败步骤。

三条省 token 的硬规矩（2026-11-22 ep10 实测后加，别绕）：
  1. 不许 `cat meta.json`——它是单行 JSON、30 KB、几乎全是带签名的 playurl 直链，
     一个有字的字段都没有多给；要标题/时长，MANIFEST 已经打出来了。
  2. 读图前先看 `rank` 表——它给出每一帧「投影页/板书/空板/人物镜头」分类 + 板书满不满、
     有没有人头挡着、字多不多。ep10 那次盲读 18 张只用上 10 张，白烧约 1 万 token。
  3. 长任务**前台**跑，用 ops_bash + 大 timeout；别再 Start-Process 起后台作业
     （子进程会跟着作业一起被带走，日志 0 字节，白跑一轮）。fetch 全程 7 分钟上下。
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(ROOT)
# 可移植配置（不绑定任何机器路径）：
#   COURSE_MEDIA   素材根目录，默认 ./tools/media/course
#   COURSE_OUT     讲义产物根目录，默认 ./course-notes
#   COURSE_NOTES   已有讲义库根目录（可选；给了就去这里找 _course.json）
#   COURSE_PY      python 解释器，默认当前解释器
PY = os.environ.get("COURSE_PY") or sys.executable
MEDIA = os.environ.get("COURSE_MEDIA") or os.path.join(PROJ, "tools", "media", "course")
OUT = os.environ.get("COURSE_OUT") or os.path.join(PROJ, "course-notes")
NOTES = os.environ.get("COURSE_NOTES") or ""
MEDIA = os.path.join(PROJ, "tools", "media", "course")


def log(msg, err=False):
    """进度日志。`err=True` 时走 stderr——`--json` 模式下必须这样，
    否则日志行会混进 stdout，`| Out-File x.json` 直接变成非法 JSON
    （2026-11-24 ep18 踩过：`scan --json` 的 `[scan] 299 帧…` 那一行把整份 JSON 污染了）。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr if err else sys.stdout,
          flush=True)


def mmss(t):
    """秒 → mm:ss（模块级；cmd_nums 里那个是局部的，别混）"""
    t = int(t)
    return "%02d:%02d" % (t // 60, t % 60)


def run(cmd, cwd=None, timeout=1800):
    """沙箱禁命名管道：子进程输出重定向到文件再读回（不能用 capture_output）。"""
    tmp = os.path.join(ROOT, "tmp")
    os.makedirs(tmp, exist_ok=True)
    tag = f"_cr_{os.getpid()}_{int(time.time() * 1000) % 100000}"
    fo, fe = os.path.join(tmp, tag + ".out"), os.path.join(tmp, tag + ".err")
    with open(fo, "wb") as o, open(fe, "wb") as e:
        p = subprocess.run(cmd, stdout=o, stderr=e, cwd=cwd, timeout=timeout)
    out = open(fo, "rb").read()
    err = open(fe, "rb").read()
    for f in (fo, fe):
        try:
            os.remove(f)
        except OSError:
            pass
    return p.returncode, out, err


def epdir(bv, ep):
    return os.path.join(MEDIA, f"{bv}-ep{ep}", f"p{ep}")


def course_json_for(bv):
    """找 _course.json 并确认它属于这门课（source/bvid 对得上），返回路径或 None。

    搜索顺序：COURSE_NOTES（若设置）→ COURSE_OUT。
    不写死任何个人目录名；没有 _course.json 时上游会走「无课程元数据」的降级路径。
    """
    roots = [r for r in (NOTES, OUT) if r and os.path.isdir(r)]
    for base in roots:
        for root, _dirs, files in os.walk(base):
            if "_course.json" in files:
                p = os.path.join(root, "_course.json")
                try:
                    j = json.load(open(p, encoding="utf-8"))
                except Exception:
                    continue
                if j.get("source") == bv or any(
                        e.get("bvid") == bv for e in j.get("episodes", [])):
                    return p
    return None


# ---------------------------------------------------------------- fetch

def cmd_fetch(a):
    d = epdir(a.bv, a.ep)
    os.makedirs(d, exist_ok=True)
    steps, fails = [], []

    def step(name, argv, ok_check=None):
        t0 = time.time()
        rc, out, err = run(argv)
        ok = (rc == 0) and (ok_check() if ok_check else True)
        dt = time.time() - t0
        steps.append((name, ok, dt))
        tail = (out or err).decode("utf-8", "ignore").strip().split("\n")[-1][:90]
        log(f"{'OK ' if ok else 'FAIL'} {name} {dt:.0f}s  {tail}")
        if not ok:
            fails.append(name)
        return ok

    prep = [PY, os.path.join(ROOT, "course_prep.py")]
    force_flag = ["--force"] if a.force else []
    step("meta", prep + ["meta", a.bv, str(a.ep)] + force_flag,
         lambda: os.path.isfile(os.path.join(d, "meta.json")))
    step("audio", prep + ["audio", a.bv, str(a.ep)] + force_flag,
         lambda: os.path.isfile(os.path.join(d, "audio.m4a")))

    # 转写（长任务，一条命令等到底；不再手动轮询）
    txt = os.path.join(d, "transcript.txt")
    audio = os.path.join(d, "audio.m4a")
    if os.path.isfile(txt) and os.path.getsize(txt) > 1000 and not a.force:
        log(f"skip transcribe（已有 {os.path.getsize(txt)} B）")
        steps.append(("transcribe", True, 0.0))
    elif not os.path.isfile(audio):
        fails.append("transcribe(无音频)")
    else:
        env_cmd = [PY, os.path.join(ROOT, "transcribe.py"), audio,
                   a.model, os.path.join(d, "transcript")]
        if a.prompt:
            env_cmd.append(a.prompt)
        env = os.environ.copy()
        env["WHISPER_BEAM"] = str(a.beam)
        env["WHISPER_THREADS"] = str(a.threads)
        t0 = time.time()
        tmp = os.path.join(ROOT, "tmp")
        os.makedirs(tmp, exist_ok=True)
        fo = os.path.join(tmp, "_cr_transcribe.log")
        with open(fo, "wb") as f:
            p = subprocess.run(env_cmd, stdout=f, stderr=f, env=env)
        dt = time.time() - t0
        ok = p.returncode == 0 and os.path.isfile(txt)
        steps.append(("transcribe", ok, dt))
        log(f"{'OK ' if ok else 'FAIL'} transcribe {dt:.0f}s  "
            f"（日志 {fo}）")
        if not ok:
            fails.append("transcribe")

    if not a.no_video:
        step("video", prep + ["video", a.bv, str(a.ep), "--quality", str(a.quality)] + force_flag,
             lambda: os.path.isfile(os.path.join(d, "video.mp4")))
        fdir = os.path.join(d, "frames")
        have = len([f for f in os.listdir(fdir) if f.endswith(".png")]) if os.path.isdir(fdir) else 0
        if have >= a.min_frames and not a.force:
            log(f"skip frames（已有 {have} 张）")
            steps.append(("frames", True, 0.0))
        else:
            step("frames", prep + ["frames", os.path.abspath(d), "--every", str(a.every)],
                 lambda: len(os.listdir(fdir)) >= 10)
        # 帧时刻核验（生成 frames.csv）
        csv = os.path.join(d, "frames.csv")
        if not (os.path.isfile(csv) and not a.force):
            step("verify", [PY, os.path.join(ROOT, "course_verify.py"), "frames",
                            os.path.join(d, "video.mp4"), fdir, "--by-name", "--out", csv],
                 lambda: os.path.isfile(csv))
        else:
            log("skip verify（已有 frames.csv）")
            steps.append(("verify", True, 0.0))
        # 候选帧排序表：读图前先看它，别盲读（ep10 盲读 18 张只用上 10 张）
        rc, out, err = run([PY, os.path.abspath(__file__), "rank", os.path.abspath(d),
                            "--every", str(a.every), "--top", "8"])
        if rc == 0:
            print(out.decode("utf-8", "ignore").rstrip())
        else:
            log("rank 失败（不影响成稿，可手动重跑）")

    # ---- MANIFEST：一段读完，不用再 ls
    print("\n" + "=" * 60)
    print(f"MANIFEST  {a.bv} ep{a.ep}  →  {os.path.relpath(d, PROJ)}")
    for f, label in [("meta.json", "元数据"), ("audio.m4a", "音频"),
                     ("transcript.txt", "转写"), ("transcript.srt", "字幕"),
                     ("video.mp4", "视频"), ("frames.csv", "帧核验表")]:
        p = os.path.join(d, f)
        if os.path.isfile(p):
            print(f"  {label:8s} {f:16s} {os.path.getsize(p)/1e6:8.2f} MB")
    fdir = os.path.join(d, "frames")
    if os.path.isdir(fdir):
        n = len([x for x in os.listdir(fdir) if x.endswith(".png")])
        print(f"  {'抽帧':8s} frames/          {n} 张（文件名 = 真实秒，f_NNNN）")
    mp = os.path.join(d, "meta.json")
    if os.path.isfile(mp):
        try:
            m = json.load(open(mp, encoding="utf-8"))
            print(f"  时长 {m.get('duration', 0):.0f}s  标题 {m.get('title','')[:60]}")
        except Exception:
            pass
    tp = os.path.join(d, "transcript.srt")
    if os.path.isfile(tp):
        n = len(re.findall(r'^\d+$', io.open(tp, encoding='utf-8').read(), flags=re.M))
        print(f"  转写 {n} 段")
    print(f"  失败步骤 {fails or '无'}")
    print("-" * 60)
    print("  下一步：")
    print("    1) 挑帧只照上面的 rank 表来，读图预算 10 张（board 取最满、投影取最满）")
    print("    2) 要转写梗概跑 `course_run.py digest <媒体目录>`；LLM 摘要那层别再要")
    print("    3) 别 cat meta.json：单行 30 KB、全是 playurl 直链，标题时长上面已有")
    print("=" * 60)
    return 0 if not fails else 1


# ---------------------------------------------------------------- shots

def _board_box(path, thr=0.06, pad=0.07):
    """在「远景」帧里自动找黑板包围盒（黑板墨绿：g>r+4 且 g>b+4 且不太亮）。

    为什么要这个：教室全景里黑板只占画面一小块，直接整帧缩放放大倍数不够、
    字糊；先框出黑板再放大，才能读到粉笔字。实测 ep15 收尾 5 帧远景，
    框出来是 (239,227,1095,441)，放大 3 倍后 `Ro'=rbe/(1+β)` 这类下标能读清。

    thr = 行/列上绿色像素占多大比例才算"属于板面"，调大→框更紧。
    返回 (x1,y1,x2,y2)；找不到板面返回 None（调用方应回退整帧）。
    """
    from PIL import Image
    import numpy as _np
    a = _np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    m = (g > r + 4) & (g > b + 4) & (g < 150)
    H, W = m.shape
    rows, cols = m.sum(1), m.sum(0)
    ys = _np.where(rows > W * thr)[0]
    xs = _np.where(cols > H * thr)[0]
    if not len(ys) or not len(xs):
        return None
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    ph, pw = (y1 - y0) * pad, (x1 - x0) * pad
    box = (max(0, int(x0 - pw)), max(0, int(y0 - ph)),
           min(W, int(x1 + pw)), min(H, int(y1 + ph)))
    # 质量闸：**只判断「框出来有没有意义」**，不再判断「这是不是黑板」。
    #
    # 2026-11-23 ep17 空跑推翻了原来那三道闸门（bw/bh>=3.0、bh>=0.15H、bw>=0.4W）：
    # 它们是用 ep16 的 5 帧扁横带拟合的，换一集就误杀——
    #   f_0620(投影) bw/bh=1.61、f_2400(板书) 2.68、f_2740(远景) 1.71 全被拒，
    #   而这 3 帧板面分别占 97%/66%/78%，裁了明显有用。
    # 「拒绝人物帧」这件事**已由 scan 的类别判断接管**（cmd_look 里人物/废帧直接
    # 不裁），所以这里不必再靠几何量区分人物。
    #
    # 只留一条：框几乎等于整帧时裁了等于没裁，返回 None 让调用方原样出图。
    #
    # 2026-11-23 ep17 空跑又推翻了一个候选判据「框内 edge 太低 = 不是板面」。
    # 实测（ep16 真值，框内边缘密度）：
    #   板书 mean 4.6（2.2–14.6）／投影 4.5／远景 7.4／**人物 7.7**
    # 人物反而最高——头发、眼镜、衣领的对比度远高于粉笔字。
    # 这跟 green 是同一个错：拿「画面复杂度」当「有字」的代理，方向就是反的。
    # 结论：**缺 scan 类别时，任何像素统计都不能可靠判断「该不该裁」**，
    # 所以 cmd_look 在无 scan 表时改为「不裁、只缩」，不再猜。
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw <= 0 or bh <= 0:
        return None
    # 2026-11-25 ep26 补：太小的框一定是噪声框（ep26 的 f_1800 就被判成 4×2 px），
    # 这种框拿去裁只会得到几个像素。真板面实测占 25%–97%，1% 的下限不会误杀。
    if bw * bh < 0.01 * W * H:
        return None
    if bw * bh >= 0.92 * W * H:
        return None
    return box


def _proj_box(path, thr_r=0.20, thr_c=0.20, fill=0.50):
    """在「投影页」帧里自动找投影幕包围盒（投影页：白/浅底、低饱和、成块矩形）。

    为什么要这个（2026-11-25 ep26 踩）：`auto` 只认墨绿黑板。遇到 44:50 那种
    「画面主体是投影页」的时刻，拿板书矩形去裁出来是一张空板，只能删掉重裁
    —— 那就是 course_gate 的 G2（二次裁切）唯一一条 FAIL 的来源。

    判据与 _board_box 正好相反：投影是**亮**的（min 通道 > 140）且**低饱和**
    （max-min < 45）；先按行/列占比缩到幕面，再过四道几何闸。
    返回 (x1,y1,x2,y2)；不像投影页时返回 None（调用方回退整帧）。

    实测（ep26）：f_2690 → (150,180,1181,716)、f_2440 → (26,10,1002,336) 都框对；
    f_1800 板书帧返回 None（正确回退整帧）。
    """
    from PIL import Image
    import numpy as _np
    a = _np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = _np.maximum(_np.maximum(r, g), b)
    mn = _np.minimum(_np.minimum(r, g), b)
    m = (mn > 140) & ((mx - mn) < 45)
    H, W = m.shape
    rows, cols = m.sum(1), m.sum(0)
    ys = _np.where(rows > W * thr_r)[0]
    xs = _np.where(cols > H * thr_c)[0]
    if not len(ys) or not len(xs):
        return None
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    if bw <= 0 or bh <= 0:
        return None
    # 闸 1：幕面不能几乎等于整帧（裁了等于没裁）
    if bw * bh >= 0.92 * W * H:
        return None
    # 闸 2：幕面要够大（太小多半只是白纸、白衬衫、白墙的一角）
    if bw < 0.30 * W or bh < 0.22 * H:
        return None
    # 闸 3：长宽比要像一块幕（16:9≈1.78 / 16:10≈1.60 / 4:3≈1.33）
    ar = bw / float(bh)
    if not (1.10 <= ar <= 2.60):
        return None
    # 闸 4：框内要"填满"亮像素（散落的白墙、天花板靠这条拒掉）
    if m[y0:y1 + 1, x0:x1 + 1].mean() < fill:
        return None
    return (x0, y0, x1 + 1, y1 + 1)


def _parse_shots(specs):
    """语法： t[:crop:x1,y1,x2,y2[:scale[:名字]]]

    例： 2280                                    → 只截取整帧
         600:::刻度示例                           → 整帧 + 名字
         2280:crop:60,55,720,480:1.6:输出特性      → 裁切 + 放大 1.6 + 名字
         2670:auto:3:共集输出电阻                  → 自动框黑板（远景帧用）+ 放大 3
         2690:proj:0:两级波特图投影                → 自动框投影幕 + 放大到长边约 2400
         2690:any:0:xxx                           → 先试黑板；不像黑板再试投影幕
    """
    out = []
    for s in specs:
        parts = s.split(":")
        t = float(parts[0])
        crop, scale, name, auto = None, 1.0, None, False
        mode = None
        rest = parts[1:]
        i = 0
        while i < len(rest):
            p = rest[i]
            if p == "crop" and i + 1 < len(rest):
                crop = [float(x) for x in rest[i + 1].split(",")]
                i += 2
                continue
            if p == "auto":                # 自动框黑板（远景帧）
                auto, mode = True, "auto"
                i += 1
                continue
            if p in ("proj", "any"):       # 自动框投影幕 / 黑板优先再投影
                mode = p
                i += 1
                continue
            if not p:                      # 空段跳过
                i += 1
                continue
            # 能解析成数字且还没有 scale → 当 scale；否则当名字
            try:
                v = float(p)
                if scale == 1.0 and name is None:
                    scale = v
                else:
                    name = p
            except ValueError:
                name = p
            i += 1
        out.append(dict(t=t, crop=crop, scale=scale, auto=auto, mode=mode,
                        name=name or f"t{int(t)}"))
    return out


def _find_media(dirpath):
    """集目录 → 媒体目录：从 _course.json 的 local 字段找，找不到就扫"""
    p = os.path.join(dirpath, "素材清单.md")
    if os.path.isfile(p):
        t = io.open(p, encoding="utf-8").read()
        m = re.search(r'`(tools[\\/]media[\\/]course[^`]+)`', t)
        if m:
            cand = os.path.join(PROJ, m.group(1).replace("\\", os.sep))
            if os.path.isdir(cand):
                return cand
    # 兜底：按集名里的 tNNNN 猜不到，扫一遍 media/course 找含该集号的
    return None


def _frame_stats(path):
    """一帧的两个观测量：亮块占比 bright（投影幕）、墨绿占比 green（黑板）。

    为什么只信这两个数（2026-11-25 ep26）：`green` 早就被证伪过（集间均值差 7 倍、
    Fisher<1，见 02-工具与踩坑.md ②-A-补），不能用来判「是不是板书」；
    但**「亮块占比」在 ep26 上是强判据**：273 帧里板书时刻 0.05–0.10、
    两页投影 0.95/0.96，中间没有教学帧。所以用它判「这一帧是不是投影页」，
    而不是判「有没有字」。
    """
    from PIL import Image
    import numpy as _np
    a = _np.asarray(Image.open(path).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = _np.maximum(_np.maximum(r, g), b)
    mn = _np.minimum(_np.minimum(r, g), b)
    bright = float(((mn > 140) & ((mx - mn) < 45)).mean())
    green = float(((g > r + 4) & (g > b + 4) & (g < 150)).mean())
    return dict(w=a.shape[1], h=a.shape[0], bright=bright, green=green)


def _kind_of(st):
    """按亮块占比给帧类型 + 该用哪种矩形。返回 (verdict, advice)。"""
    if st["bright"] >= 0.60:
        return "投影页", "整帧就是幕面：别套板书矩形；要裁就用 proj 或整帧"
    if st["bright"] <= 0.20:
        return "板书", "用板书矩形裁（shots ... crop:x1,y1,x2,y2）"
    return "存疑", "亮块 0.2–0.6，多半是教室全景/白墙：先 look --full 看一眼再定"


def _runs(ts, gap=10):
    """把时间点压成连续段：相邻间隔 <= gap 秒的合并。返回 [(lo, hi, n)]。

    为什么要这个（2026-11-25 ep26）：投影页在时间轴上是「碎段」而不是整段——
    ep26 那 26 帧散在 09:10、19:00–32:10、40:40、44:20–45:20 共 12 段。
    逐帧列出来看不出「哪几段时间屏幕上一直是一整页课件」，压成段才能一眼发现漏截。
    """
    out = []
    for t in sorted(ts):
        if out and t - out[-1][1] <= gap:
            out[-1][1] = t
            out[-1][2] += 1
        else:
            out.append([t, t, 1])
    return [(lo, hi, n) for lo, hi, n in out]


def cmd_kind(a):
    med = os.path.abspath(a.media_dir)
    fdir = os.path.join(med, "frames")
    if not os.path.isdir(fdir):
        log(f"没有 frames 目录：{fdir}")
        return 1
    ts = []
    if a.times.strip():
        for p in a.times.split(","):
            p = p.strip()
            if not p:
                continue
            ts.append(int(float(p.split(":")[0]) * 60 + float(p.split(":")[1])) if ":" in p else int(float(p)))
    else:
        ts = sorted(int(f[2:6]) for f in os.listdir(fdir)
                    if re.fullmatch(r"f_\d{4}\.png", f))
    rows = []
    for t in ts:
        p = os.path.join(fdir, "f_%04d.png" % t)
        if not os.path.isfile(p):
            rows.append(dict(t=t, bright=None, green=None, kind="缺帧", advice="删掉这个时刻再派活"))
            continue
        st = _frame_stats(p)
        k, ad = _kind_of(st)
        rows.append(dict(t=t, bright=round(st["bright"], 3),
                         green=round(st["green"], 3), kind=k, advice=ad))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    from collections import Counter
    c = Counter(r["kind"] for r in rows)
    print(f"# {len(rows)} 帧 · " + " / ".join(f"{k} {v}" for k, v in c.most_common()))
    for r in rows:
        b = "  -   " if r["bright"] is None else f"{r['bright']:.3f}"
        g = "  -   " if r["green"] is None else f"{r['green']:.3f}"
        print(f"  {r['t']:>5}s  bright {b}  green {g}  {r['kind']:<4}  {r['advice']}")
    if not a.times.strip():
        proj = [r["t"] for r in rows if r["kind"] == "投影页"]
        if proj:
            segs = _runs(proj)
            print(f"\n## 投影页时间分布：{len(proj)} 帧 / {len(segs)} 段（间隔<=10s 合并）")
            for lo, hi, n in segs:
                if hi == lo:
                    rng = f"{lo // 60:02d}:{lo % 60:02d}"
                else:
                    rng = f"{lo // 60:02d}:{lo % 60:02d}–{hi // 60:02d}:{hi % 60:02d}"
                print(f"  {lo:>5}s – {hi:>5}s  ({rng})  {n} 帧")
            print("  => 这些时段屏幕上就是整页课件：别用板书矩形裁，也别整段漏掉不截。")
        else:
            print("\n## 投影页时间分布：整片没有亮块 >=0.60 的帧（纯板书课）")
        print("\n（全片统计：只给观测量，分类结论请只对教学时刻下）")
    return 0


def cmd_shots(a):
    ep_dir = os.path.abspath(a.ep_dir)
    _m = re.search(r'(?:ep|P)(\d+)', os.path.basename(ep_dir))
    if not _m:
        log(f"目录名里找不到集号（要含 ep26 或 P26 之类）：{os.path.basename(ep_dir)}")
        return 1
    n = int(_m.group(1))
    med = a.media_dir or _find_media(ep_dir)
    if not med:
        log("找不到媒体目录，用 --media-dir 显式指定（如 tools/media/course/BV1...-ep8/p8）")
        return 1
    fdir = os.path.join(med, "frames")
    assets = os.path.join(ep_dir, "assets")
    os.makedirs(assets, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        log("需要 Pillow（tools/venv 里有）")
        return 1
    made = []
    shotlog = []
    for s in _parse_shots(a.specs):
        src = os.path.join(fdir, "f_%04d.png" % int(s["t"]))
        if not os.path.isfile(src):
            log(f"缺帧 {src}")
            continue
        im = Image.open(src)
        box = None
        mode = s.get("mode")
        if mode in ("auto", "any"):
            box = _board_box(src)
            if box is None and mode == "any":
                box = _proj_box(src)
                if box is not None:
                    log(f"t={int(s['t'])} 不是黑板，自动框投影幕 {box}")
            if box is None:
                log(f"t={int(s['t'])} 没找到黑板，回退整帧")
            else:
                log(f"t={int(s['t'])} 自动框黑板 {box}")
        elif mode == "proj":
            box = _proj_box(src)
            if box is None:
                st = _frame_stats(src)
                if st["bright"] >= 0.60:
                    log(f"t={int(s['t'])} 投影占满整帧（亮块 {st['bright']:.2f}），用整帧不裁")
                else:
                    log(f"t={int(s['t'])} 不像投影页（亮块 {st['bright']:.2f}），回退整帧")
            else:
                log(f"t={int(s['t'])} 自动框投影幕 {box}")
        if s["crop"]:
            box = tuple(int(x) for x in s["crop"])
        if box:
            im = im.crop(box)
        if s["scale"] == 0 and box:
            # scale=0 = 自动：放大到长边约 2400 px（够读下标，又不浪费体积）
            s["scale"] = round(2400.0 / max(1, max(im.size)), 2)
        if s["scale"] == 0:
            s["scale"] = 1.0      # 回退整帧且没框：不放大，原样出图
        if s["scale"] != 1.0:
            im = im.resize((int(im.width * s["scale"]),
                            int(im.height * s["scale"])), Image.LANCZOS)
        out = os.path.join(assets, f"ep{n:02d}-t{int(s['t']):04d}-{s['name']}.png")
        im.save(out)
        made.append((os.path.basename(out), im.size, os.path.getsize(out)))
        shotlog.append({
            "t": int(s["t"]),
            "box": list(box) if box else [],
            "name": s["name"],
            "file": os.path.basename(out),
        })
    print(f"\n生成 {len(made)} 张 → {os.path.relpath(assets, PROJ)}")
    for name, size, sz in made:
        print(f"  {name}  {size[0]}x{size[1]}  {sz/1024:.0f} KB")

    # shots.log：给 course_gate 的 G2（不许二次裁切）留证据。
    # 格式 TSV，每行一次裁切：时刻<TAB>x1,y1,x2,y2（空=整帧）<TAB>名字<TAB>输出文件名
    if shotlog:
        lp = os.path.join(med, "shots.log")
        new_file = not os.path.isfile(lp)
        with io.open(lp, "a", encoding="utf-8", newline="\n") as f:
            if new_file:
                f.write("# t\tbox\tname\tfile\tstamp\n")
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for r in shotlog:
                box = ",".join(str(v) for v in r["box"])
                f.write("%d\t%s\t%s\t%s\t%s\n"
                        % (r["t"], box, r["name"], r["file"], stamp))
        log("裁切记录 → %s（G2 用）" % os.path.relpath(lp, PROJ))
    if len(made) != len(a.specs):
        log(f"警告：期望 {len(a.specs)} 张，实际 {len(made)} 张")
        return 1
    return 0


# ---------------------------------------------------------------- check

def cmd_check(a):
    ep_dir = os.path.abspath(a.ep_dir)
    argv = [PY, os.path.join(ROOT, "note_check.py"), ep_dir]
    if a.tol is not None:
        argv += ["--tol", str(a.tol)]
    if a.frames_csv:
        argv += ["--frames-csv", a.frames_csv]
    rc, out, err = run(argv)
    sys.stdout.write(out.decode("utf-8", "ignore"))
    if err:
        sys.stderr.write(err.decode("utf-8", "ignore"))
    return rc


# ---------------------------------------------------------------- rank

def _frame_metrics(path):
    """一帧的四项指标（都是百分比，0–100）。阈值用 ep10 的 13 张已知帧标定过：

    green  暗绿像素率 —— "镜头正对着黑板"的证据。黑板的墨绿满足 g>r+4 且 g>b+4；
           实测开场/观众镜头 2.8–3.8%，空板 12.9%，写了字的板 8.5–26.7%
    light  亮像素率 —— 投影页整页白底会冲到 45%+；黑板上白粉笔只有 0–15%
    skin   肤色像素率 —— 有人挡板。实测观众镜头 17.6%，老师站在板前只有 1–1.6%
    edge   边缘密度 —— 字/图多不多。板书 6–11，投影页 8–17

    取景区域 = 去掉底部 28%（讲台、桌面、前景学生）和右侧 6%（台标水印）。
    """
    from PIL import Image, ImageFilter
    im = Image.open(path).convert("RGB").resize((320, 180))
    w, h = im.size
    im = im.crop((int(w * 0.02), int(h * 0.04), int(w * 0.94), int(h * 0.72)))
    px = list(im.getdata())
    n = max(1, len(px))
    green = sum(1 for r, g, b in px if g > r + 4 and g > b + 4) * 100.0 / n
    light = sum(1 for r, g, b in px if (r + g + b) / 3 > 185) * 100.0 / n
    skin = sum(1 for r, g, b in px
               if r > 95 and g > 40 and b > 20 and (max(r, g, b) - min(r, g, b)) > 15
               and abs(r - g) > 15 and r > g and r > b) * 100.0 / n
    e = list(im.convert("L").filter(ImageFilter.FIND_EDGES).getdata())
    edge = sum(1 for v in e if v > 40) * 100.0 / max(1, len(e))
    return green, light, skin, edge


def _thin(pool, gap):
    """按分数从高到低走，已选时刻 ±gap 秒内的不再要——同一块板面/同一页投影只留一张"""
    picked = []
    for r in pool:
        if any(abs(r["t"] - q["t"]) < gap for q in picked):
            continue
        picked.append(r)
    return picked


def cmd_rank(a):
    """候选帧排序表：先看表、再决定读哪几张图（读图预算 10 张）"""
    fdir = os.path.join(os.path.abspath(a.media_dir), "frames")
    if not os.path.isdir(fdir):
        log(f"找不到 {fdir}")
        return 1
    rows = []
    for name in sorted(os.listdir(fdir)):
        if not (name.startswith("f_") and name.endswith(".png")):
            continue
        try:
            t = int(name[2:-4])
        except ValueError:
            continue
        if a.every and t % a.every:
            continue
        green, light, skin, edge = _frame_metrics(os.path.join(fdir, name))
        # ------------------------------------------------------------------
        # 2026-11-23 ep16 复盘：`green` **不能**用来分「有没有板书」。
        # 证据（tools/_rank_separability.py，对 ep14/15/16 三集真值）：
        #   Fisher 可分性 = 0.35 / 0.19 / 0.43，全部远小于 1（=两类糊在一起）
        #   更致命的是 ep15：人物组 green 均值 10.7 > 板书组 5.1，方向都反了
        # 原因：green 率随该集白平衡/曝光漂移（集间均值 5.5%–48.1%，差 7 倍），
        #       且黑板被老师挡住时 green 照样低、投影页 white 高时 green 也低。
        # 故 green<7 这个固定阈值在 ep13/14/15/16/19/20 上召回只有 0.07–0.42。
        #
        # 所以这里**只出观测量，不再下 kind 结论**：kind 列保留但标 REF，
        # 真正的分类以 `course_run.py scan` 出的 frames_scan.csv 为准。
        # ------------------------------------------------------------------
        if light > 45:
            kind = "REF投影页"
            score = edge
        elif skin > 8:
            kind = "REF人物"
            score = 0.0
        elif edge < 5.8:                     # 对着板面但几乎没字（这条与色温无关，可靠）
            kind = "REF空板"
            score = 0.0
        else:
            # 可用性只由「字量」决定：字多且没人挡。不再要求 green 达标。
            kind = "REF候选"
            score = edge - skin * 0.5
        rows.append(dict(t=t, kind=kind, green=green, light=light,
                         skin=skin, edge=edge, score=score))

    csv = os.path.join(os.path.abspath(a.media_dir), "frames_rank.csv")
    with io.open(csv, "w", encoding="utf-8") as f:
        print("t,kind,green,light,skin,edge,score", file=f)
        for r in rows:
            print("%d,%s,%.1f,%.1f,%.1f,%.1f,%.1f" % (
                r["t"], r["kind"], r["green"], r["light"],
                r["skin"], r["edge"], r["score"]), file=f)

    print(f"候选帧排序表（{len(rows)} 帧，每 {a.every}s 取一张）→ "
          f"{os.path.relpath(csv, PROJ)}")
    print("  ⚠ kind 列只是**参考观测量**（REF*），不是分类结论；"
          "真正分类走 `course_run.py scan`。理由见本函数内注释。")

    # 2026-11-23 ep16 复核：只靠 edge 排序也**不能**用来挑帧。
    # 实证：删掉 green 判据后，按 score（=edge）排前 4 名是 f_2600(人物)、
    # f_0040(开场)、f_2310(人物)、f_2540(人物)——全是无板面的帧，因为 edge 只量
    # 「画面有多少边」，人脸轮廓和字幕框的边比粉笔字还多。
    # 所以这里**不输出推荐排名**，只按时段给观测量供对照；
    # 挑帧走 scan 的段落表，或 --near 按知识点定位。
    if a.list:
        print()
        print(f"  按 {a.gap}s 抽稀后的观测量（不做推荐，仅供对照）：")
        pool = [r for r in rows if r["kind"] in ("REF候选", "REF投影页")]
        pool.sort(key=lambda r: r["t"])
        shown = _thin(pool, a.gap)
        for r in shown[:a.top]:
            mm, ss = divmod(r["t"], 60)
            print(f"    {mm:02d}:{ss:02d}  f_{r['t']:04d}  {r['kind']:<8}"
                  f"板面 {r['green']:5.1f}%  亮 {r['light']:5.1f}%  "
                  f"遮挡 {r['skin']:4.1f}%  字量 {r['edge']:4.1f}")
        print(f"    （共 {len(shown)} 个时刻，只显示前 {a.top}；"
              f"全部在 frames_rank.csv）")
    for kind in ("REF空板", "REF人物"):
        cnt = sum(1 for r in rows if r["kind"] == kind)
        if cnt:
            print(f"  参考剔除 {kind} {cnt} 帧（不作为分类依据）")
    print("  → 挑帧请用 `course_run.py scan <媒体目录>` 出段落表，"
          "或 `rank --near 10:20,22:00` 按知识点定位。")

    # ---- 盲区检查：连续一段时间没有任何「板书/投影页」帧 ⇒ 分类器可能漏判了
    #   ep13 实测：24:00 那块全微分板书被判成「非板面」，整段推导差点漏掉。
    #   判据不看单帧，看「连续无可用帧的时长」，超过 a.gap_alert 秒就点名报警。
    usable = sorted(r["t"] for r in rows if r["kind"] in ("REF候选", "REF投影页"))
    if usable:
        holes = []
        prev = usable[0]
        for t in usable[1:]:
            if t - prev >= a.gap_alert:
                holes.append((prev, t))
            prev = t
        for lo, hi in holes:
            l1, l2 = divmod(lo, 60)
            h1, h2 = divmod(hi, 60)
            # 空洞中间抓两帧点名，让人知道该去看哪儿
            mids = [lo + (hi - lo) * k // 3 for k in (1, 2)]
            tags = []
            for m in mids:
                near = min(rows, key=lambda r: abs(r["t"] - m))
                tags.append(f"f_{near['t']:04d}({near['kind']})")
            print()
            print(f"  [盲区] {l1:02d}:{l2:02d}–{h1:02d}:{h2:02d} "
                  f"连续 {hi - lo}s 没有可用候选帧，分类器可能漏判 → 建议抽看 "
                  + "、".join(tags))

    # --near：给知识点时间点，直接在窗口内挑最好的帧（最常用，一句话就出结果）
    if a.near:
        print()
        print(f"  按知识点时间点挑帧（窗口 ±{a.window}s，每个点前 {a.per} 张）：")
        for target in _parse_times(a.near):
            win = [r for r in rows if abs(r["t"] - target) <= a.window
                   and r["kind"] in ("REF候选", "REF投影页")]
            win.sort(key=lambda r: -r["score"])
            mm, ss = divmod(target, 60)
            if not win:
                print(f"    {mm:02d}:{ss:02d}  —— 窗口内没有可用的候选帧")
                continue
            best = win[:a.per]
            desc = "  ".join(
                f"f_{r['t']:04d}({r['kind']}, 分{r['score']:.1f})" for r in best)
            print(f"    {mm:02d}:{ss:02d}  →  {desc}")
    return 0


def _parse_times(spec):
    """'1530,25:30,1740' → [1530, 1530, 1740]；支持秒数和 mm:ss"""
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if ":" in part:
            m, s = part.split(":")
            out.append(int(m) * 60 + int(float(s)))
        else:
            out.append(int(float(part)))
    return out


# ---------------------------------------------------------------- nums

def _read_srt(path):
    """→ [(blk, t秒, 物理行号, 文本)]；物理行号 = 该块首行在文件里的 1-based 行号"""
    lines = io.open(path, encoding="utf-8-sig").read().splitlines()
    out, i, blk = [], 0, 0
    while i < len(lines):
        if (re.match(r"^\d+\s*$", lines[i].strip()) and i + 1 < len(lines)
                and "-->" in lines[i + 1]):
            m = re.search(r"(\d+):(\d+):(\d+)", lines[i + 1])
            t = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                 if m else 0)
            blk += 1
            ph = i + 1                     # 1-based
            buf, j = [], i + 2
            while j < len(lines) and lines[j].strip() and "-->" not in lines[j]:
                buf.append(lines[j].strip())
                j += 1
            out.append((blk, t, ph, " ".join(buf)))
            i = j
        else:
            i += 1
    return out


def cmd_nums(a):
    """一次列出**所有可能含数值/符号**的行，省掉来回 grep。

    2026-11-23 ep16 复盘：那一集我把「倍 / 通频带 / 北台 / 左右」分四轮 grep，
    每轮都回十几块同样的上下文。核数本来就该**一次全出**，然后在结果里挑。

    默认输出两条流：
      1) 数字行  —— 任何含阿拉伯数字或中文数字的块（电压/电阻/倍数/温度都在这）
      2) 关键词  —— --kw 给的词命中（用来抓转写惯错，如 rbe/RE/β 的各种听错）
    行号一律是 srt 的**物理行号**，可以直接在复查表里引用。
    """
    med = os.path.abspath(a.media_dir)
    srt = os.path.join(med, "transcript.srt")
    if not os.path.isfile(srt):
        log(f"找不到 {srt}")
        return 1
    blocks = _read_srt(srt)

    def mmss(t):
        return "%d:%02d" % (t // 60, t % 60)

    def show(b):
        blk, t, ph, txt = b
        print(f"L{ph:<5} blk{blk:<5} {mmss(t):>6}  {txt}")

    # 阿拉伯数字一律算；中文数字只认「数量词」形态，且排除「一个/一种/一样」这类口语。
    # 裸匹配 [一二三…] 是 267/1214 块，收紧后仍被「一个」占掉大半，故再排除。
    num_re = re.compile(
        r"[0-9０-９]"
        r"|(?<![第每某这那])[一二三四五六七八九十百千万亿两]{1,3}"
        r"(?:倍|个|种|级|端|分贝|次|伏|安|欧|赫|学期|章|节)"
        r"|(?:[几幾][十百千]|(?:十|二十)[几幾]|三分之一)"
        # 「百分之五十」后面不跟量词，上面那条抓不到（ep17 25:21 遗忘曲线那句就漏了）
        r"|百分之[一二三四五六七八九十百零]+"
    )
    _ok = re.compile(r"(?:一个|一种|一样|一次|一直|一些|一般|一起|一半|一边|一定)$")

    def is_num(txt):
        return any(not _ok.search(txt[:m.end()]) for m in num_re.finditer(txt))
    hits = [b for b in blocks if is_num(b[3])]
    print(f"# 数字行 {len(hits)} / {len(blocks)} 块   （{mmss(blocks[0][1])}"
          f"–{mmss(blocks[-1][1])}）")
    print("#" + "=" * 78)
    for b in hits:
        show(b)

    kws = [k.strip() for k in (a.kw or "").replace("，", ",").split(",") if k.strip()]
    if kws:
        print(f"\n# 关键词 {'/'.join(kws)}")
        print("#" + "=" * 78)
        seen = set()
        for b in blocks:
            got = [k for k in kws if k in b[3]]
            if got and b[2] not in seen:
                seen.add(b[2])
                print(f"[{'|'.join(got)}] ", end="")
                show(b)
    print(f"\n# 共 {len(blocks)} 块 / 文件 {len(blocks) * 3 + len(blocks)} 行；"
          f"引用时用 L<物理行号>")
    return 0


# ---------------------------------------------------------------- look

def cmd_look(a):
    """读图前先裁：把要看的帧裁到板面，出一张小图。

    为什么强制这一步（2026-11-23 ep16 复盘，最贵的一条教训）：
    `read_image` 是**整帧进上下文**的。ep16 那张 `f_2400` 是 1280x720 / 352 KB，
    而板面只占中间约 55%——我一次读了 22 张这样的整帧，合计 7.6 MB ≈ 3 万 token，
    其中 6 张还是同一个板面的近重复。
    裁到板面后单张约 120 KB，且**放大 2 倍反而更清楚**（字更大了）。
    so：**先 look 再 read_image**，不要直接 read 原帧。

    板面框用 `_board_box`（同一套判据）。框不到就退回整帧，并明说。

    2026-11-23 ep17 空跑修正：**裁剪策略优先读 `frames_scan.csv`，不靠几何猜**。
    起因：ep17 六帧全部「框不到」，查下来是 `_board_box` 的 `bw/bh >= 3.0`
    把一帧黑板占 60% 画面的板书帧拒了（该集板面更高，不是扁横带）。
    用 ep16 真值标定后确认：**几何量单独无法区分「远景」和「人物」**
    （人物帧 `f_2030` 的框形和板书几乎一样），任何几何闸门都会误伤一类。
    而调用 `look` 时**已经知道**该时刻属于哪一类（scan 的段落表给了），
    所以直接按 scan 的类别决定：
      板书/投影 → 裁板面（尺寸最省，字最清楚）
      远景     → 裁板面 + 强制放大（板面小，不放大读不清）
      人物/废帧 → 不裁，只缩（裁了也没内容可读）
    没有 scan 表时退回几何判据，并提示「先跑 scan」。
    """
    med = os.path.abspath(a.media_dir)
    fdir = os.path.join(med, "frames")
    outdir = a.out or os.path.join(med, "look")
    os.makedirs(outdir, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        log("需要 Pillow（tools/venv 里有）")
        return 1

    # 读 scan 表（有就用，没有就退回几何猜）
    kinds = {}
    sp = os.path.join(med, "frames_scan.csv")
    if os.path.isfile(sp) and not a.no_scan:
        import csv as _csv
        try:
            for r in _csv.DictReader(io.open(sp, encoding="utf-8-sig")):
                kinds[(r.get("frame") or "").replace(".png", "")] = r.get("kind") or ""
        except Exception:
            kinds = {}
    if not kinds:
        log("没有 frames_scan.csv：只能整帧缩小（不裁）。"
            "**先跑 `course_run.py scan` 出段落表**，否则远景的板面裁不出来。")

    specs = []
    for tok in a.times.replace(" ", "").split(","):
        if not tok:
            continue
        t = _parse_times(tok)[0] if ":" in tok else int(float(tok))
        specs.append(t)

    made, missed = [], []
    for t in specs:
        tag = "f_%04d" % t
        src = os.path.join(fdir, tag + ".png")
        if not os.path.isfile(src):
            missed.append((t, "缺帧"))
            continue
        kind = kinds.get(tag, "")
        # 按类别决定裁剪策略；**没有类别信息时不裁**（见 _board_box 注释：
        # 像素统计无法判断该不该裁，硬猜会裁出人头，且看不出裁错了）。
        if a.full or not kind or kind in ("人物", "废帧"):
            box = None                      # 不裁
        else:
            box = _board_box(src)
        im = Image.open(src)
        full_size = im.size
        if box:
            im = im.crop(box)
        # 远景：板面小，裁出来之后再放大一点才读得清。但**不能放大过头**——
        # ep16 的 f_2740 试过放大到长边 2000，出来 867 KB，比原帧 400 KB 还大，
        # 白烧 token 而字并不会更清楚（源就 952 px 宽，超采样不增加信息）。
        # 用同一个 1200 上限即可：裁剪本身已经让字变大 1.26 倍。
        base_max = a.max
        # 只缩不放大：超采样不增加信息，只会把文件撑大
        # （ep16 的 f_2740 裁出 952x558 又拉到 1200 宽，结果 445 KB 比原帧还大）。
        # 裁剪本身已经把字放大了（板面占比 45% → 100%），不需要再缩放开大。
        if base_max and max(im.size) > base_max:
            k = base_max / float(max(im.size))
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                           Image.LANCZOS)
        out = os.path.join(outdir, tag + ("_board" if box else "_full") + ".png")
        # PNG 重编码有时比原帧还大（原帧是调色板/低色深）。退整帧时**优先 JPEG**：
        # 这类帧是照片，JPEG 体积能降一大截，而读图质量够用。
        if not box and a.full_jpeg:
            out = out[:-4] + ".jpg"
            im.convert("RGB").save(out, quality=a.quality, optimize=True)
        else:
            im.save(out, optimize=True)
        made.append((t, tag, box, full_size, im.size, os.path.getsize(out), out))

    total = sum(m[5] for m in made)
    # --json 模式：报告一律走 stderr，stdout 只留给 JSON（否则整份 JSON 被报告行污染，
    # 2026-11-24 排查 scan --json 时发现 look 也有同款问题）。
    report = sys.stderr if a.json else sys.stdout
    for t, tag, box, fs, os_, sz, out in made:
        mm, ss = divmod(t, 60)
        kind = kinds.get(tag, "")
        if box:
            note = f"框 {box}"
        else:
            note = f"{kind or '未知'}：不裁，退整帧" if kind in ("人物", "废帧") \
                else "框不到板面，退整帧"
        print(f"  {mm:02d}:{ss:02d}  {tag}  {fs[0]}x{fs[1]} → {os_[0]}x{os_[1]}"
              f"  {sz/1024:5.0f} KB  {note}", file=report)
    if missed:
        for t, why in missed:
            mm, ss = divmod(t, 60)
            print(f"  {mm:02d}:{ss:02d}  —— {why}", file=report)
    print(f"\n{len(made)} 张 → {os.path.relpath(outdir, PROJ)}"
          f"  合计 {total/1024:.0f} KB", file=report)
    if a.json:
        # file 用真实落盘路径（退整帧 + --full-jpeg 时是 .jpg，不是 .png）
        print(json.dumps([{"t": m[0], "file": m[6], "box": m[2],
                           "w": m[4][0], "h": m[4][1], "bytes": m[5]}
                          for m in made], ensure_ascii=False))
    return 0 if made else 1


# ---------------------------------------------------------------- digest

def cmd_digest(a):
    """机械稿（去口头禅 + 按 2 分钟切块 + 留时间戳）。**不加 --llm**：
    LLM 摘要那层和子智能体的知识点卡重复，ep10 实测 23 条一条没用上。"""
    argv = [PY, os.path.join(ROOT, "course_digest.py"), os.path.abspath(a.media_dir)]
    rc, out, err = run(argv)
    sys.stdout.write(out.decode("utf-8", "ignore"))
    if err:
        sys.stderr.write(err.decode("utf-8", "ignore"))
    return rc


def cmd_scan(a):
    """视觉子代理判帧：纯本地两段（出清单 / 并结果），转调 course_scan.py。

    为什么不在脚本里直接打 API：沙箱内直连 dashscope 实测返回
    `<400> InternalError.Algo: Temporary multimodal download failure: TimeoutError`，
    所以读图仍由会话里的 workflow 起子代理跑，这里只做本地的事。
    """
    argv = [PY, os.path.join(ROOT, "course_scan.py"),
            "merge" if a.result else "list", os.path.abspath(a.media_dir)]
    if a.result:
        argv += [os.path.abspath(a.result), "--smooth", str(a.smooth)]
    else:
        argv += ["--batch", str(a.batch)]
        if a.json:
            argv.append("--json")
    rc, out, err = run(argv)
    sys.stdout.write(out.decode("utf-8", "ignore"))
    if err:
        sys.stderr.write(err.decode("utf-8", "ignore"))
    return rc


# ---------------------------------------------------------------- brief

BRIEF_PROMPT = """用 read_image 读这张课程截图：{path}

只描述**画面结构**。**不要抄任何公式、符号、数字的内容**——抄了会被丢弃，
而且会把后面挑帧的判断带偏（ep14 实测：139 帧抄回来的全是黑板右上角那句不变的
"已知/求"，真正在写的直流通路一条没抄到）。

按这个格式回纯 JSON（不要代码块、不要解释、不要多余文字）：
{{"page":"板书|投影|远景|人物|废帧","sheet":"有|无","parts":["画面里有哪几块东西，每块2到6个字"],"read":"清晰|被挡|模糊|字太小","conf":"高|中|低"}}

sheet = 画面里有没有**整页图 / 整幅曲线 / 完整电路图**（有写"有"，只有零散几行字写"无"）。
read  = 这张图**字能不能一次读清**：被老师挡住、拍虚了、字太小，都如实写。
conf  = 你对 page 和 sheet 这两个判断有多确定；拿不准就写"低"，**不许猜**。

注意：远景 和 人物 都**不是**废帧。"""


def _scan_kinds(med):
    """读 frames_scan.csv → {f_0330: '投影'}；没有表就返回空 dict"""
    sp = os.path.join(med, "frames_scan.csv")
    out = {}
    if not os.path.isfile(sp):
        return out
    try:
        import csv as _csv
        for r in _csv.DictReader(io.open(sp, encoding="utf-8-sig")):
            out[(r.get("frame") or "").replace(".png", "")] = r.get("kind") or ""
    except Exception:
        return {}
    return out


def cmd_brief(a):
    """子代理**只描述画面结构**，主模型据此决定自己读哪几张。

    为什么要这一层（2026-11-24 ep17 复盘）：
      这一集我读了 17 张图，其中只有 3 张是"不读就会写错"的
      （`t1650` 三面板哪个极接地、`t1470` 共集/共基那句谁错、`t1380` 作对照）。
      另外 14 张是"为了看清细节"读的，而细节里 6 张是重复板面、4 张老师挡板。

    为什么**不能**连文字一起外包（同一集和 ep14 都验证过）：
      子代理读手写板面时会锁死在画面里最容易读的那个角落，返回"看起来每帧都有
      信息、实际全是同一句"的假稠密表——**比 null 更危险**（null 会去重读）。

    所以分界线是固定的：
      可以外包：有没有成页的图 / 有几块要素 / 可读性 / 置信度
      不能外包：公式原文、符号下标、箭头方向  → 这些必须主模型亲眼看

    用法（两段，同 `scan`）：
      brief <媒体目录> --times 450,540,1650            # 出提示词+清单
      brief <媒体目录> --times ... --result 结果.json   # 并表，标出"必读/可跳过"
    """
    med = os.path.abspath(a.media_dir)
    fdir = os.path.join(med, "frames")
    kinds = _scan_kinds(med)

    ts = []
    for tok in (a.times or "").replace(" ", "").split(","):
        if not tok:
            continue
        ts.append(_parse_times(tok)[0] if ":" in tok else int(float(tok)))
    if not ts:
        log("--times 是空的（逗号分隔的时刻，如 450,540 或 07:30,09:00）")
        return 2

    # 先夹一次：本集没有这个时刻的帧就报出来，别派给子代理
    # （2026-11-24 ep18：`--times 3050` 派了一张不存在的 f_3050.png，
    #   子代理如实回报"文件不存在"，白跑一格，还容易被误当成子代理出错）
    have = [t for t in ts if os.path.isfile(os.path.join(fdir, "f_%04d.png" % t))]
    miss = [t for t in ts if t not in have]
    if miss:
        fs = sorted(int(x[2:6]) for x in os.listdir(fdir)
                    if x.startswith("f_") and x.endswith(".png"))
        log("⚠ 没有这些时刻的帧，已跳过：%s（本集帧 %s–%s）"
            % ("、".join(mmss(t) for t in miss),
               mmss(fs[0]) if fs else "?", mmss(fs[-1]) if fs else "?"), err=a.json)
        ts = have
    if not ts:
        log("一个可用时刻都没有")
        return 2

    if not a.result:
        if a.json:
            print(json.dumps({"dir": fdir + os.sep,
                              "frames": ["f_%04d.png" % t for t in ts],
                              "prompt": BRIEF_PROMPT}, ensure_ascii=False, indent=1))
            return 0
        print("=" * 72)
        print("提示词（逐帧替换 {path}）：")
        print("=" * 72)
        print(BRIEF_PROMPT.format(path="<媒体目录>/frames/f_0450.png"))
        print()
        print("=" * 72)
        print("帧清单（%d 个）：" % len(ts))
        print("=" * 72)
        for t in ts:
            print("f_%04d.png  %s  [%s]" % (t, mmss(t), kinds.get("f_%04d" % t, "?")))
        print()
        log("跑完存 JSON，再 `brief <媒体目录> --times ... --result 结果.json`")
        return 0

    # ---- 并表模式
    raw = io.open(os.path.abspath(a.result), encoding="utf-8").read()
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            log("结果 JSON 解析不了")
            return 1
        data = json.loads(m.group(0))
    if isinstance(data, dict):
        data = data.get("frames") or data.get("brief") or list(data.values())
    byt = {}
    for r in data:
        if not isinstance(r, dict):
            continue
        t = r.get("t")
        if isinstance(t, str) and ":" in t:
            t = _parse_times(t)[0]
        try:
            t = int(float(t))
        except (TypeError, ValueError):
            continue
        byt[t] = r

    rows, must, may, drop = [], [], [], []
    for t in ts:
        tag = "f_%04d" % t
        r = byt.get(t) or {}
        has = os.path.isfile(os.path.join(fdir, tag + ".png"))
        sheet = str(r.get("sheet", "")).strip()
        conf = str(r.get("conf", "")).strip() or "?"
        read = str(r.get("read", "")).strip()
        parts = r.get("parts") or []
        parts = [str(x) for x in parts][:3]
        # 建议三档，**不替你决定**，只把"废帧"和"必看"分开：
        #   弃用  = 被挡 / 拍虚 → 这张没用，换一个时刻，根本不用读
        #   必读  = 字太小（不是废帧，是**该放大**：配 `look` 裁完再读）
        #           或 置信低（子代理拿不准，要我自己判）或 有成页图（要抄公式符号）
        #   按需  = 其余（多半是干净的板书页，读不读由"歧义点数"决定）
        if not has:
            adv = "缺帧"
        elif read in ("被挡", "模糊"):
            adv = "**弃用**"
            drop.append(t)
        elif read == "字太小" or conf in ("低", "?") or sheet == "有":
            adv = "**必读**"
            must.append(t)
        else:
            adv = "按需"
            may.append(t)
        rows.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            mmss(t), tag, kinds.get(tag, "?"), r.get("page", "?"), sheet or "?",
            "、".join(parts) or "—", read or "?", conf, adv))

    print("| 时刻 | 帧 | scan类别 | 子代理判 | 成页图 | 要素 | 可读 | 置信 | 建议 |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        print(r)
    print()
    log("必读 %d / 弃用 %d / 按需 %d（共 %d）"
        % (len(must), len(drop), len(may), len(ts)))
    log("规则：**主模型读的张数 = 会让讲义写错的歧义点数 + 1**，不是固定 10 张。"
        "子代理的描述只用来挑帧，**一个字都不许进讲义正文**。")
    if drop:
        log("弃用时刻（换时刻重挑）：" + ",".join(mmss(t) for t in drop))
    if must:
        log("必读时刻：" + ",".join(mmss(t) for t in must))
    return 0


# ---------------------------------------------------------------- main

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="course_run.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("fetch", help="meta+音频+转写+视频+抽帧+帧核验 一条命令")
    pf.add_argument("bv")
    pf.add_argument("ep", type=int)
    pf.add_argument("--every", type=int, default=10)
    pf.add_argument("--quality", type=int, default=720)
    pf.add_argument("--model", default="small")
    pf.add_argument("--beam", type=int, default=1)
    pf.add_argument("--threads", type=int, default=14)
    pf.add_argument("--prompt", default=None, help="whisper 术语提示词")
    pf.add_argument("--no-video", action="store_true", help="只要音频+转写")
    pf.add_argument("--min-frames", type=int, default=50,
                    help="帧数达到这个值就跳过抽帧（断点续跑）")
    pf.add_argument("--force", action="store_true", help="全部重做")
    pf.set_defaults(func=cmd_fetch)

    ps = sub.add_parser("shots", help="挑好的帧 → assets（裁切/放大/命名）")
    ps.add_argument("ep_dir")
    ps.add_argument("specs", nargs="+",
                    help="t[:crop:x1,y1,x2,y2[:scale[:名字]]]")
    ps.add_argument("--media-dir", default=None)
    ps.set_defaults(func=cmd_shots)

    pc = sub.add_parser("check", help="笔记自检（转调 note_check.py）")
    pc.add_argument("ep_dir")
    pc.add_argument("--tol", type=int, default=None)
    pc.add_argument("--frames-csv", default=None)
    pc.set_defaults(func=cmd_check)

    pk = sub.add_parser("kind", help="裁图前先判帧类型（投影页/板书/存疑），别拿错矩形")
    pk.add_argument("media_dir")
    pk.add_argument("--times", default="", help="逗号分隔时刻：2440,2690；不给就统计全片")
    pk.add_argument("--json", action="store_true")
    pk.set_defaults(func=cmd_kind)

    pl = sub.add_parser("look", help="读图前先裁到板面（出一张小图，别直接 read 原帧）")
    pl.add_argument("media_dir")
    pl.add_argument("times", help="逗号分隔时刻：620,1320 或 10:20,22:00")
    pl.add_argument("--max", type=int, default=1200, help="输出长边像素（默认 1200）")
    pl.add_argument("--full", action="store_true", help="不裁，只缩")
    pl.add_argument("--full-jpeg", action="store_true", dest="full_jpeg",
                    default=True, help="框不到时退整帧用 JPEG（默认开，体积小得多）")
    pl.add_argument("--quality", type=int, default=88, help="JPEG 质量（默认 88）")
    pl.add_argument("--out", default=None, help="输出目录（默认 <媒体目录>/look）")
    pl.add_argument("--json", action="store_true")
    pl.add_argument("--no-scan", action="store_true", dest="no_scan",
                    help="忽略 frames_scan.csv，强制用几何判据裁（不推荐）")
    pl.set_defaults(func=cmd_look)

    pn = sub.add_parser("nums", help="一次列出所有含数字/关键词的块（核数不要分轮 grep）")
    pn.add_argument("media_dir")
    pn.add_argument("--kw", default="", help="额外关键词，逗号分隔（抓转写惯错）")
    pn.set_defaults(func=cmd_nums)

    pr = sub.add_parser("rank", help="候选帧排序表（读图前先看这张表）")
    pr.add_argument("media_dir")
    pr.add_argument("--every", type=int, default=10)
    pr.add_argument("--top", type=int, default=10)
    pr.add_argument("--list", action="store_true",
                    help="列出观测量对照（不做推荐排名；挑帧请用 scan）")
    pr.add_argument("--gap", type=int, default=40,
                    help="去重间隔秒数：已选时刻 ±gap 内不再选（默认 40）")
    pr.add_argument("--near", default=None,
                    help="知识点时间点，逗号分隔（支持 1530 或 25:30），每个点给出窗口内最好的帧")
    pr.add_argument("--window", type=int, default=120, help="--near 的窗口半径秒数（默认 120）")
    pr.add_argument("--per", type=int, default=2, help="--near 每个点给几张（默认 2）")
    pr.add_argument("--gap-alert", type=int, default=180, dest="gap_alert",
                    help="盲区报警：连续多少秒没有任何板书/投影页帧就点名（默认 180，即 3 分钟）")
    pr.set_defaults(func=cmd_rank)

    pd_ = sub.add_parser("digest", help="机械稿（不含 LLM 摘要，省一步）")
    pd_.add_argument("media_dir")
    pd_.set_defaults(func=cmd_digest)

    ps_ = sub.add_parser("scan", help="视觉子代理判帧：出清单 / 并结果（转调 course_scan.py）")
    ps_.add_argument("media_dir")
    ps_.add_argument("--result", default=None,
                     help="子代理结果 JSON；不给就是出清单模式")
    ps_.add_argument("--batch", type=int, default=6)
    ps_.add_argument("--smooth", type=int, default=1)
    ps_.add_argument("--json", action="store_true")
    ps_.set_defaults(func=cmd_scan)

    pb = sub.add_parser("brief", help="子代理只描述画面结构；主模型据此决定自己读几张")
    pb.add_argument("media_dir")
    pb.add_argument("--times", required=True,
                    help="候选帧时刻，逗号分隔（450,540 或 07:30,09:00）")
    pb.add_argument("--result", default=None,
                    help="子代理结果 JSON；不给就是出清单模式")
    pb.add_argument("--json", action="store_true")
    pb.set_defaults(func=cmd_brief)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
