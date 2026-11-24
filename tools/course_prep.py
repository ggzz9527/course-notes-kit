#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""course_prep.py —— 拉课素材预检/下载（音频优先，视频按需）

设计原则（2026-11-21 定）：
  1) **音频优先**：转写只要 20~25 MB，先下音频就能出稿，不必等整集视频；
  2) **视频按需**：只有要抽帧截图时才下视频；可只保留需要的时间段（--keep）；
  3) 坏节点自动绕：B 站江苏节点会整段 TLS 超时，这里统一把 host 换成可达 mirror，多线程分段下载；
  4) 每一步都打时间戳与字节数，避免"没输出=看起来死了"。

用法：
  py course_prep.py meta   <BV> <ep>                  # 只取标题/时长/各流直链，落 meta.json
  py course_prep.py audio  <BV> <ep>                  # 只下音频 → <out>/audio.m4a（默认就该先跑这个）
  py course_prep.py video  <BV> <ep> [--keep 300-360,900-930] [--quality 720]
  py course_prep.py frames <dir> --every 10           # 等间隔抽帧（素材池）
  py course_prep.py frames <dir> --at 300,600,900     # 按知识点时间点抽帧（讲义用）
  py course_prep.py verify <dir>                      # 调 course_verify.py frames --by-name

目录约定：<out> = tools/media/course/<BV>-ep<N>/p<N>/
退出码：0 成功；1 失败（打印原因）。
"""
import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(ROOT)

# 可移植配置（不绑定任何机器路径）：
#   COURSE_MEDIA   素材根目录，默认 ./tools/media/course
#   COURSE_PY      python 解释器，默认当前解释器（需已装 yt-dlp）
#   COURSE_FFMPEG  ffmpeg 路径，默认 ./bin/ffmpeg.exe，找不到就退回 PATH 里的 ffmpeg
MEDIA = (os.environ.get("COURSE_MEDIA")
         or os.path.join(PROJ, "tools", "media", "course"))
PY = os.environ.get("COURSE_PY") or sys.executable
_ff = os.environ.get("COURSE_FFMPEG") or os.path.join(ROOT, "bin", "ffmpeg.exe")
FF = _ff if os.path.exists(_ff) else "ffmpeg"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HDR = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
MIRRORS = ["upos-sz-mirror08c.bilivideo.com", "upos-sz-mirrorhw.bilivideo.com",
           "upos-sz-mirrorcos.bilivideo.com", "upos-sz-mirrorali.bilivideo.com"]
WORKERS = 4


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_to_files(cmd):
    """沙箱禁命名管道：不能 capture_output，改成子进程输出重定向到文件再读回。
    返回 (returncode, stdout_bytes, stderr_bytes)。"""
    tmpd = os.environ.get("COURSE_TMP") or os.path.join(PROJ, "tools", "tmp")
    os.makedirs(tmpd, exist_ok=True)
    tag = f"_prep_{os.getpid()}_{int(time.time()*1000) % 100000}"
    fo, fe = os.path.join(tmpd, tag + ".out"), os.path.join(tmpd, tag + ".err")
    with open(fo, "wb") as o, open(fe, "wb") as e:
        p = subprocess.run(cmd, stdout=o, stderr=e)
    try:
        with open(fo, "rb") as f:
            out = f.read()
        with open(fe, "rb") as f:
            err = f.read()
    finally:
        for f in (fo, fe):
            try:
                os.remove(f)
            except OSError:
                pass
    return p.returncode, out, err


def media_dir(bv, ep):
    d = os.path.join(MEDIA, f"{bv}-ep{ep}", f"p{ep}")
    os.makedirs(d, exist_ok=True)
    return d


def meta_path(d):
    return os.path.join(d, "meta.json")


def fetch_meta(bv, ep, force=False):
    d = media_dir(bv, ep)
    mp = meta_path(d)
    if os.path.isfile(mp) and not force:
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("_at", 0) > time.time() - 3600:
            log(f"用缓存 meta（1 小时内）: {meta['title'][:40]}")
            return meta
    url = f"https://www.bilibili.com/video/{bv}?p={ep}"
    log(f"取 meta: {url}")
    rc, out, err = run_to_files([PY, "-m", "yt_dlp", "-J", "--no-warnings", url])
    if rc != 0:
        log("yt-dlp 取 meta 失败: " + err[-300:].decode("utf-8", "ignore"))
        return None
    meta = json.loads(out.decode("utf-8", "ignore"))
    meta["_at"] = time.time()
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    log(f"标题: {meta['title']}\n         时长: {meta['duration']:.0f}s")
    return meta


AUDIO_PREF = ["30216", "30232", "30280", "30250", "30251"]


def pick_format(meta, kind, quality=720):
    """挑流：kind=audio|video。
    - 优先非 mcdn 流；非 mcdn 池挑不到时**回退 mcdn 池**（mcdn 直链实测 206 可下，
      只有「换 mirror」才会 403）——2026-11-24 ep21 音频流全是 mcdn 时踩过；
    - 音频按 format_id 优先（30216 就够转写），不按 filesize（很多流没报大小）；
    - 视频按 height ≤ quality 取最大，同高按 filesize 取大。"""
    fs_all = list(meta["formats"])
    fs = [f for f in fs_all if "mcdn" not in (f.get("url") or "")]
    if kind == "audio":
        # 2026-11-24 ep21：某些集**所有音频流都是 mcdn**，无条件剔 mcdn 会得到
        # "没有音频流"。改为：先非 mcdn 池，找不到再回退 mcdn 池（mcdn 直链实测 206 可下）。
        for pool in (fs, fs_all):
            for fid in AUDIO_PREF:
                for f in pool:
                    if f.get("format_id") == fid:
                        return f
            cands = [f for f in pool if f.get("vcodec") in (None, "none")]
            if cands:
                return cands[0]
        return None
    # 视频同款回退：非 mcdn 池里没有 ≤quality 的流时，回退 mcdn 池。
    for pool in (fs, fs_all):
        vids = [f for f in pool if f.get("vcodec") not in (None, "none")]
        vids = [f for f in vids if (f.get("height") or 0) <= quality]
        if vids:
            vids.sort(key=lambda f: (f.get("height") or 0, f.get("filesize") or 0),
                      reverse=True)
            return vids[0]
    return None


def _probe(url):
    try:
        req = urllib.request.Request(url, headers={**HDR, "Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return len(r.read(256))
    except Exception:
        return 0


def resolve_urls(fmt):
    """把 baseUrl/backupUrl 里能连的那个换成 mirror（签名随路径，换 host 有效）。

    2026-11-24 ep21 补：mcdn 源换 mirror 会 403，全部 mirror 都不通时
    **回退原始直链**（mcdn 直链本身可 206 分段），否则会报"所有 mirror 都连不上"。"""
    urls = [fmt.get("url")] + list(fmt.get("backup_urls") or [])
    for m in MIRRORS:
        for u in urls:
            if not u:
                continue
            mu = re.sub(r"//[^/]+/", "//" + m + "/", u, count=1)
            if _probe(mu):
                return mu
    for u in urls:
        if u and _probe(u):
            return u
    return None


def download(url, dest, workers=WORKERS):
    """多线程 Range 分段下载；服务器不支持 Range 时退回单流。"""
    tmp = dest + ".part"
    head = urllib.request.Request(url, headers={**HDR, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(head, timeout=10) as r:
            cr = r.headers.get("Content-Range") or ""
    except Exception as e:
        log(f"探测失败 {type(e).__name__}: {e}")
        return False
    total = 0
    m = re.search(r"/(\d+)$", cr)
    if m:
        total = int(m.group(1))
    if total <= 0:
        log("服务器不支持 Range，改单流下载")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HDR),
                                        timeout=60) as r, open(tmp, "wb") as f:
                while True:
                    b = r.read(1 << 18)
                    if not b:
                        break
                    f.write(b)
            os.replace(tmp, dest)
            return True
        except Exception as e:
            log(f"单流下载失败: {e}")
            return False

    chunk = total // workers + 1
    done = [0] * workers
    errs = []

    def work(i):
        s = i * chunk
        e = min(s + chunk - 1, total - 1)
        if s > e:
            return
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url, headers={**HDR, "Range": f"bytes={s}-{e}"})
                with urllib.request.urlopen(req, timeout=60) as r, \
                        open(f"{tmp}.{i}", "wb") as f:
                    while True:
                        b = r.read(1 << 18)
                        if not b:
                            break
                        f.write(b)
                        done[i] += len(b)
                return
            except Exception as ex:                       # noqa: BLE001
                log(f"  分片 {i} 第 {attempt+1} 次失败: {type(ex).__name__}")
        errs.append(i)

    t0 = time.time()
    ts = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
    [t.start() for t in ts]
    while any(t.is_alive() for t in ts):
        time.sleep(2)
        got = sum(done)
        log(f"  {got/1e6:6.1f}/{total/1e6:.1f} MB  "
            f"{got/1e6/max(time.time()-t0,0.001):5.2f} MB/s")
    [t.join() for t in ts]
    if errs:
        log(f"有分片失败: {errs}")
        return False
    with open(tmp, "wb") as out:
        for i in range(workers):
            pi = f"{tmp}.{i}"
            if os.path.isfile(pi):
                with open(pi, "rb") as f:
                    out.write(f.read())
                os.remove(pi)
    os.replace(tmp, dest)
    dt = time.time() - t0
    log(f"完成 {total/1e6:.1f} MB / {dt:.1f}s = {total/1e6/dt:.2f} MB/s → {os.path.basename(dest)}")
    return True


def run_ff(args):
    rc, _, err = run_to_files([FF, "-y", "-hide_banner", "-loglevel", "error"] + args)
    if rc != 0:
        log("ffmpeg 失败: " + err[-300:].decode("utf-8", "ignore"))
    return rc == 0


def cmd_meta(a):
    m = fetch_meta(a.bv, a.ep, force=a.force)
    return 0 if m else 1


def cmd_audio(a):
    d = media_dir(a.bv, a.ep)
    dest = os.path.join(d, "audio.m4a")
    if os.path.isfile(dest) and os.path.getsize(dest) > 1 << 20 and not a.force:
        log(f"已有 {dest}（{os.path.getsize(dest)/1e6:.1f} MB），跳过")
        return 0
    meta = fetch_meta(a.bv, a.ep)
    if not meta:
        return 1
    fmt = pick_format(meta, "audio")
    if not fmt:
        log("没有音频流")
        return 1
    url = resolve_urls(fmt)
    if not url:
        log("所有 mirror 和原始直链都连不上")
        return 1
    log(f"音频源 {fmt['format_id']} @ {url.split('/')[2]}")
    tmp = os.path.join(d, "_a.m4s")
    if not download(url, tmp):
        return 1
    ok = run_ff(["-i", tmp, "-vn", "-c:a", "copy", dest])
    os.remove(tmp)
    if ok:
        log(f"音频就绪: {os.path.getsize(dest)/1e6:.1f} MB（转写只需要它，先跑 "
            f"transcribe.py 出稿，视频等要截图时再下）")
    return 0 if ok else 1


def _parse_keep(s):
    out = []
    for part in s.split(","):
        a, b = part.split("-")
        out.append((float(a), float(b)))
    return out


def cmd_video(a):
    d = media_dir(a.bv, a.ep)
    dest = os.path.join(d, "video.mp4")
    if os.path.isfile(dest) and os.path.getsize(dest) > 10 << 20 and not a.force:
        log(f"已有 {dest}（{os.path.getsize(dest)/1e6:.1f} MB），跳过")
        return 0
    meta = fetch_meta(a.bv, a.ep)
    if not meta:
        return 1
    fmt = pick_format(meta, "video", a.quality)
    if not fmt:
        log(f"没有 ≤{a.quality}P 的视频流")
        return 1
    url = resolve_urls(fmt)
    if not url:
        log("所有 mirror 和原始直链都连不上")
        return 1
    log(f"视频源 {fmt['format_id']} {fmt.get('height')}P @ {url.split('/')[2]}")
    vtmp = os.path.join(d, "_v.m4s")
    if not download(url, vtmp):
        return 1
    if a.keep:
        # 只保留需要的片段：先合并音视频，再按段裁切（-c copy 不重编码）
        atmp = os.path.join(d, "_a.m4s")
        afmt = pick_format(meta, "audio")
        aurl = resolve_urls(afmt) if afmt else None
        if aurl and download(aurl, atmp):
            pass
        tmpfull = os.path.join(d, "_full.mp4")
        if os.path.isfile(atmp):
            run_ff(["-i", vtmp, "-i", atmp, "-c", "copy", tmpfull])
        else:
            run_ff(["-i", vtmp, "-c", "copy", tmpfull])
        keep = _parse_keep(a.keep)
        total = 0
        for idx, (s, e) in enumerate(keep, 1):
            clip = os.path.join(d, f"clip{idx}_{int(s)}-{int(e)}.mp4")
            run_ff(["-ss", str(s), "-to", str(e), "-i", tmpfull,
                    "-c", "copy", clip])
            if os.path.isfile(clip):
                total += os.path.getsize(clip)
                log(f"  片段 {clip} {os.path.getsize(clip)/1e6:.1f} MB")
        for f in (vtmp, atmp, tmpfull):
            if os.path.isfile(f):
                os.remove(f)
        log(f"按需片段合计 {total/1e6:.1f} MB（未保留整集）")
        return 0
    atmp = os.path.join(d, "_a.m4s")
    afmt = pick_format(meta, "audio")
    aurl = resolve_urls(afmt) if afmt else None
    ok = True
    if aurl:
        ok = download(aurl, atmp) and run_ff(
            ["-i", vtmp, "-i", atmp, "-c", "copy", "-movflags", "+faststart", dest])
        if os.path.isfile(atmp):
            os.remove(atmp)
    else:
        ok = run_ff(["-i", vtmp, "-c", "copy", dest])
    os.remove(vtmp)
    if ok:
        log(f"视频就绪: {os.path.getsize(dest)/1e6:.1f} MB")
    return 0 if ok else 1


def cmd_frames(a):
    d = a.dir if os.path.isabs(a.dir) else os.path.join(PROJ, a.dir)
    video = os.path.join(d, "video.mp4")
    if not os.path.isfile(video):
        clips = [f for f in os.listdir(d) if f.startswith("clip") and f.endswith(".mp4")]
        if not clips:
            log("目录里没有 video.mp4，也没有 clip*.mp4")
            return 1
        video = os.path.join(d, sorted(clips)[0])
        log(f"用片段抽帧: {os.path.basename(video)}")
    fdir = os.path.join(d, "frames")
    os.makedirs(fdir, exist_ok=True)
    if a.at:
        times = [float(x) for x in a.at.split(",")]
    elif a.every:
        _, dout, _ = run_to_files(
            [FF.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
             "-show_entries", "format=duration", "-of", "csv=p=0", video])
        dur = float(dout.decode().strip() or 0)
        times = [float(t) for t in range(0, int(dur) + 1, a.every)]
    else:
        log("要给 --every N 或 --at t1,t2,...")
        return 1
    log(f"抽 {len(times)} 帧（-ss 精确 seek）")
    for n, t in enumerate(times, 1):
        out = os.path.join(fdir, "f_%04d.png" % int(t))
        if os.path.isfile(out) and os.path.getsize(out) > 1000:
            continue
        run_ff(["-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", out])
        if n % 20 == 0:
            log(f"  {n}/{len(times)}")
    log(f"帧就绪: {len([f for f in os.listdir(fdir) if f.endswith('.png')])} 张")
    return 0


def cmd_verify(a):
    d = a.dir if os.path.isabs(a.dir) else os.path.join(PROJ, a.dir)
    video = os.path.join(d, "video.mp4")
    if not os.path.isfile(video):
        log("video.mp4 不在（片段抽帧可跳过正式核验）")
        return 1
    p = subprocess.run([PY, os.path.join(ROOT, "course_verify.py"), "frames",
                        video, os.path.join(d, "frames"), "--by-name",
                        "--out", os.path.join(d, "frames.csv")])
    return p.returncode


def main():
    ssl._create_default_https_context = ssl._create_unverified_context
    ap = argparse.ArgumentParser(prog="course_prep.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, need in (("meta", cmd_meta, True), ("audio", cmd_audio, True),
                           ("video", cmd_video, True)):
        p = sub.add_parser(name)
        p.add_argument("bv")
        p.add_argument("ep", type=int)
        if name in ("meta", "audio", "video"):
            p.add_argument("--force", action="store_true",
                           help="即使目标文件已存在也重新下载")
        if name == "video":
            p.add_argument("--keep", default=None,
                           help="只保留这些时间段，如 300-360,900-930")
            p.add_argument("--quality", type=int, default=720)
        p.set_defaults(func=fn)
    pf = sub.add_parser("frames")
    pf.add_argument("dir")
    pf.add_argument("--every", type=int, default=None)
    pf.add_argument("--at", default=None)
    pf.set_defaults(func=cmd_frames)
    pv = sub.add_parser("verify")
    pv.add_argument("dir")
    pv.set_defaults(func=cmd_verify)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
