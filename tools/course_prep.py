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
def _resolve_tool(env_key, local_name, cmd_name):
    """外部工具的单一来源：环境变量 > 包内 bin/ > PATH 命令名。

    「工具在哪」只回答一次。以前 FF / FP 分散在三处各拼一次，答案互不一致。
    """
    p = os.environ.get(env_key)
    if p:
        return p
    local = os.path.join(ROOT, "bin", local_name)
    return local if os.path.exists(local) else cmd_name


def _sibling(ff, name):
    """从 ffmpeg 的路径推出 ffprobe：同目录 + 同扩展名；纯命令名就换名字。

    2026-11-25 踩：以前是 ff.replace("ffmpeg.exe", "ffprobe.exe")。只要 ff 是
    "ffmpeg"（按 README 装进 PATH 就是这样）或 "ffmpeg.EXE"（大写），替换就落空，
    于是拿 ffmpeg 去跑 ffprobe 的参数，ffmpeg 报 Unrecognized option 'show_entries'。
    判据：这条必须能处理三种形态——"ffmpeg" / "/usr/bin/ffmpeg" / "C:\\x\\ffmpeg.EXE"。
    """
    d, base = os.path.split(ff)
    stem, ext = os.path.splitext(base)
    if not stem:
        return name
    # 大小写不敏感：Windows 上 ffmpeg.EXE / FFMPEG.EXE 是同一支程序，而 str.replace
    # 区分大小写——全大写时替换落空，又拿 ffmpeg 去跑 ffprobe 的参数，原 bug 原样复活。
    if "ffmpeg" in stem.lower():
        cand = re.sub("ffmpeg", name, stem, flags=re.IGNORECASE) + ext
    else:
        # 不是 ffmpeg 起名（自建 my-ff.exe 之类）：同目录换名字，保留扩展名
        cand = name + ext
    return os.path.join(d, cand) if d else cand


FF = _resolve_tool("COURSE_FFMPEG", "ffmpeg.exe", "ffmpeg")
FP = os.environ.get("COURSE_FFPROBE") or _sibling(FF, "ffprobe")
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
    try:
        with open(fo, "wb") as o, open(fe, "wb") as e:
            p = subprocess.run(cmd, stdout=o, stderr=e)
    except OSError as e:
        # 「工具没装 / 路径写错」是使用者最常见的错误，别丢 traceback 给他。
        # rc 用 127（shell 的 command not found 约定），错误信息走 stderr 通道回传。
        for f_ in (fo, fe):
            try:
                os.remove(f_)
            except OSError:
                pass
        msg = "不能执行 %s：%s" % (cmd[0], e)
        return 127, b"", msg.encode("utf-8", "ignore")
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
        # 2026-11-26：核验已有产物只需要 duration。网络挂了不该把一份完整文件判成失败，
        # 所以取不到最新 meta 时退回盘上那份（哪怕过期）。
        if os.path.isfile(mp):
            try:
                with open(mp, encoding="utf-8") as f:
                    meta = json.load(f)
                if meta.get("duration"):
                    log(f"取 meta 失败，改用缓存 meta（{meta.get('title', '')[:40]}）")
                    return meta
            except (OSError, ValueError):
                pass
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


def _cleanup(paths):
    """删临时产物；删不掉不算失败（下一次进来还会被 _stale_parts 清）。"""
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _stale_parts(dest):
    """枚举 dest 这一族的残留：`x.part`、`x.part.0` … `x.part.N`。"""
    d = os.path.dirname(dest) or "."
    base = os.path.basename(dest) + ".part"
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return [os.path.join(d, n) for n in names
            if n == base or n.startswith(base + ".")]


def download(url, dest, workers=WORKERS):
    """多线程 Range 分段下载；服务器不支持 Range 时退回单流。

    判据（2026-11-26，反例见 scripts/regress_download.py）：
        不存在「报成功但落盘文件是残的」的可达状态。
    所以每一段都必须核对「写出去的字节数 == Content-Range 声明的字节数」，
    单流必须核对 Content-Length；对不上就是失败，**绝不拼文件**——
    以前丢一段也会拼出一个「看起来完整」的文件，要到转写出乱码才发现。
    上一轮的分片先清空：workers 数变过时，旧的 `.part.4..7` 会被当成本次结果拼进去。
    """
    tmp = dest + ".part"
    _cleanup(_stale_parts(dest))
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
        return _download_single(url, dest, tmp)

    chunk = total // workers + 1
    done = [0] * workers
    errs = []

    def work(i):
        s = i * chunk
        e = min(s + chunk - 1, total - 1)
        if s > e:
            return
        want = e - s + 1
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url, headers={**HDR, "Range": f"bytes={s}-{e}"})
                got = 0
                with urllib.request.urlopen(req, timeout=60) as r, \
                        open(f"{tmp}.{i}", "wb") as f:
                    while True:
                        b = r.read(1 << 18)
                        if not b:
                            break
                        f.write(b)
                        got += len(b)
                        done[i] = got
                if got == want:
                    return
                # 少给算失败；多给也算——Content-Range 说要 want 字节就得是 want 字节
                log(f"  分片 {i} 第 {attempt+1} 次字节数不符: {got}/{want}")
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
        log(f"有分片失败: {errs}——不拼文件")
        _cleanup([tmp] + [f"{tmp}.{i}" for i in range(workers)])
        return False
    written = 0
    with open(tmp, "wb") as out:
        for i in range(workers):
            pi = f"{tmp}.{i}"
            if os.path.isfile(pi):
                with open(pi, "rb") as f:
                    while True:
                        b = f.read(1 << 20)
                        if not b:
                            break
                        out.write(b)
                        written += len(b)
                os.remove(pi)
    if written != total:
        log(f"拼接后总字节 {written} != 预期 {total}——不落盘")
        _cleanup([tmp])
        return False
    os.replace(tmp, dest)
    dt = time.time() - t0
    log(f"完成 {total/1e6:.1f} MB / {dt:.1f}s = {total/1e6/dt:.2f} MB/s → {os.path.basename(dest)}")
    return True


def _download_single(url, dest, tmp):
    """单流下载（服务器不支持 Range 时）。Content-Length 与实际 body 不符就算失败。"""
    want, got = 0, 0
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HDR),
                                    timeout=60) as r, open(tmp, "wb") as f:
            cl = r.headers.get("Content-Length") or ""
            want = int(cl) if cl.isdigit() else 0
            while True:
                b = r.read(1 << 18)
                if not b:
                    break
                f.write(b)
                got += len(b)
    except Exception as e:
        log(f"单流下载失败: {type(e).__name__}: {e}")
        _cleanup([tmp])
        return False
    if want and got != want:
        log(f"单流字节数不符: 实收 {got} != Content-Length {want}——不落盘")
        _cleanup([tmp])
        return False
    if got <= 0:
        log("单流下载 0 字节——服务器给了个空响应")
        _cleanup([tmp])
        return False
    os.replace(tmp, dest)
    log(f"完成 {got/1e6:.1f} MB（单流）→ {os.path.basename(dest)}")
    return True


def run_ff(args):
    rc, _, err = run_to_files([FF, "-y", "-hide_banner", "-loglevel", "error"] + args)
    if rc != 0:
        log("ffmpeg 失败: " + err[-300:].decode("utf-8", "ignore"))
    return rc == 0


DUR_TOL = 1.0   # 秒：成品容器时长与 meta.duration 允许的差


def probe_duration(path):
    """读容器时长（秒）；读不出来返回 None。"""
    rc, out, err = run_to_files(
        [FP, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path])
    if rc != 0:
        return None
    try:
        d = float(out.decode("utf-8", "ignore").strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
    return d if d > 0 else None


def verify_duration(path, want, tol=DUR_TOL):
    """成品核验：容器时长与 meta.duration 相差 ≤ tol 秒才算数。

    为什么不能只看 ffmpeg 的 rc：`-c copy` 对截断的 m4s **可能返回 0**，
    产出的 audio.m4a / video.mp4 是半个文件，rc 看不出来；体积更是猜不出来。
    判据只能落在成品本身：读一次时长。
    """
    if not os.path.isfile(path):
        return False, "文件不在"
    if os.path.getsize(path) <= 0:
        return False, "0 字节"
    got = probe_duration(path)
    if got is None:
        return False, "ffprobe 读不出时长（文件残或不是可识别的容器）"
    if want and abs(got - want) > tol:
        return False, "时长 %.2fs 与 meta %.2fs 差 %.2fs > %.0fs" % (
            got, want, abs(got - want), tol)
    return True, "时长 %.2fs" % got


def _id_path(dest):
    return dest + ".id.json"


def _write_id(dest, duration, format_id=""):
    """写下这次核过的产物身份（字节数 + 时长）。下次靠它认出「被截断/替换过」。"""
    try:
        with open(_id_path(dest), "w", encoding="utf-8") as f:
            json.dump({"size": os.path.getsize(dest),
                       "duration": round(duration or 0, 3),
                       "format_id": format_id}, f)
    except OSError:
        pass


def verify_artifact(dest, want, tol=DUR_TOL):
    """成品判据：时长核得过 **且** 与上次核过的字节数一致。

    ffprobe 的时长来自容器头——半个文件只要 moov 还在，时长照样报全长。
    所以字节数只与**自己上次核过的记录**比，绝不拿「体积够大」当完整：
    后者在低码率、残件、换了码率的集上都错。
    没有身份记录时退化为只核时长（兼容旧素材），核过后补写身份。
    """
    ok, why = verify_duration(dest, want, tol)
    if not ok:
        return False, why
    ip = _id_path(dest)
    if os.path.isfile(ip):
        try:
            with open(ip, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            rec = None
        if rec and rec.get("size") and rec["size"] != os.path.getsize(dest):
            return False, "字节数 %d != 上次核过的 %d（被截断/替换过）" % (
                os.path.getsize(dest), rec["size"])
    return True, why


def cmd_meta(a):
    m = fetch_meta(a.bv, a.ep, force=a.force)
    return 0 if m else 1


def _fetch_to(kind, d, meta, want, quality=720):
    """下载 + 合并 + **成品核验**，落盘到 d/audio.m4a 或 d/video.mp4。

    返回 (rc, dest)。rc=0 的语义只有一个：**文件在那儿，且 ffprobe 时长核过**。
    任何一步没核过都删掉残品再返回非 0——不留「看起来完整」的半成品给下一次继承。
    """
    dest = os.path.join(d, "audio.m4a" if kind == "audio" else "video.mp4")
    fmt = pick_format(meta, kind, quality)
    if not fmt:
        log("没有音频流" if kind == "audio" else f"没有 ≤{quality}P 的视频流")
        return 1, dest
    url = resolve_urls(fmt)
    if not url:
        log("所有 mirror 和原始直链都连不上")
        log("  修法：确认本机能访问 upos-*.bilivideo.com / 原始直链；"
            "本机代理若拦了它，先关代理再跑。稍后重跑也行——每次都会重新挑源。")
        return 1, dest
    log(f"{'音频' if kind == 'audio' else '视频'}源 {fmt['format_id']} @ {url.split('/')[2]}")
    tmp = os.path.join(d, "_a.m4s" if kind == "audio" else "_v.m4s")
    if not download(url, tmp):
        _cleanup([tmp])
        return 1, dest
    if kind == "audio":
        ok = run_ff(["-i", tmp, "-vn", "-c:a", "copy", dest])
        _cleanup([tmp])
    else:
        atmp = os.path.join(d, "_a.m4s")
        afmt = pick_format(meta, "audio")
        aurl = resolve_urls(afmt) if afmt else None
        if aurl and download(aurl, atmp):
            ok = run_ff(["-i", tmp, "-i", atmp, "-c", "copy",
                         "-movflags", "+faststart", dest])
        else:
            log("没拿到音频流，只落视频轨")
            ok = run_ff(["-i", tmp, "-c", "copy", "-movflags", "+faststart", dest])
        _cleanup([tmp, atmp])
    if not ok:
        _cleanup([dest])
        return 1, dest
    good, why = verify_artifact(dest, want)
    if not good:
        # 状态机 VERIFY 态：ffmpeg rc=0 不代表文件是完整的（-c copy 对截断流可能 rc=0）。
        log(f"成品核验不过：{why}——删掉残品，不当成功")
        _cleanup([dest, _id_path(dest)])
        return 1, dest
    _write_id(dest, probe_duration(dest) or want, fmt["format_id"])
    log(f"{os.path.basename(dest)} 就绪: {os.path.getsize(dest)/1e6:.1f} MB（{why}）")
    return 0, dest


def cmd_audio(a):
    d = media_dir(a.bv, a.ep)
    dest = os.path.join(d, "audio.m4a")
    meta = fetch_meta(a.bv, a.ep)
    if not meta:
        return 1
    want = float(meta.get("duration") or 0)
    if not a.force:
        # 幂等判据不是「体积够大」，是「时长核得过」。体积判据会把半个文件当完整的。
        good, why = verify_artifact(dest, want)
        if good:
            log(f"已有 {dest}（{why}），跳过")
            return 0
        if os.path.isfile(dest):
            log(f"已有 {dest} 不可用（{why}）——当作残品重下")
    rc, _ = _fetch_to("audio", d, meta, want)
    if rc == 0:
        log("（转写只需要它，先跑 transcribe.py 出稿，视频等要截图时再下）")
    return rc


def _parse_keep(s):
    out = []
    for part in s.split(","):
        a, b = part.split("-")
        out.append((float(a), float(b)))
    return out


def _cut_clips(d, src, keep_spec):
    """按时间段裁切（-c copy 不重编码）。返回 0 表示每段都真出了非空文件。"""
    keep = _parse_keep(keep_spec)
    total = 0
    for idx, (s, e) in enumerate(keep, 1):
        clip = os.path.join(d, f"clip{idx}_{int(s)}-{int(e)}.mp4")
        run_ff(["-ss", str(s), "-to", str(e), "-i", src, "-c", "copy", clip])
        if not os.path.isfile(clip) or os.path.getsize(clip) <= 0:
            log(f"片段 {clip} 没产出——按段裁切失败")
            return 1
        total += os.path.getsize(clip)
        log(f"  片段 {clip} {os.path.getsize(clip)/1e6:.1f} MB")
    log(f"按需片段合计 {total/1e6:.1f} MB（未保留整集）")
    return 0


def cmd_video(a):
    d = media_dir(a.bv, a.ep)
    dest = os.path.join(d, "video.mp4")
    meta = fetch_meta(a.bv, a.ep)
    if not meta:
        return 1
    want = float(meta.get("duration") or 0)
    if a.keep:
        # --keep 的产物是片段，不是整集：不能拿「已有 video.mp4」当完成
        good, why = verify_artifact(dest, want)
        fresh = False
        if good:
            log(f"已有完整 {dest}（{why}），直接按段裁切")
        else:
            if os.path.isfile(dest):
                log(f"已有 {dest} 不可用（{why}）——当作残品重下")
            rc, dest = _fetch_to("video", d, meta, want, a.quality)
            if rc != 0:
                return rc
            fresh = True
        rc = _cut_clips(d, dest, a.keep)
        if fresh:
            # --keep 的意义就是别留整集；身份文件跟着一起删，
            # 否则下次会拿它去核一个已经不存在的文件
            _cleanup([dest, _id_path(dest)])
        return rc
    if not a.force:
        good, why = verify_artifact(dest, want)
        if good:
            log(f"已有 {dest}（{why}），跳过")
            return 0
        if os.path.isfile(dest):
            log(f"已有 {dest} 不可用（{why}）——当作残品重下")
    rc, _ = _fetch_to("video", d, meta, want, a.quality)
    return rc


def _png_ok(p):
    """产物判据：真出了一张 PNG，而不是「体积够大」。

    2026-11-25 二修：原来是 getsize > 1000。但低熵画面压出来很小——纯红 320x240
    只有 799 字节、720P 纯白 4.3 KB，合法帧被误判成「没抽出来」，整集抽帧 exit 1。
    体积猜不出来，只能看文件头：PNG 签名 8 字节，对得上就是真帧（截断/零字节对不上）。
    """
    try:
        if os.path.getsize(p) <= 0:
            return False
        with open(p, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


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

    # 产物身份（2026-11-25 二修）：只判「本次调用返回值」不够。
    # 目录里可能躺着上一支视频的帧，文件名一模一样，于是「抽 1 帧」报出「3 张 / exit 0」。
    # 判据：帧必须有身份证。抽帧时把源视频（名/大小/mtime）写进 .source.json，
    # 进来先对身份证；对不上（含「有图但没身份证」）就整目录作废重抽。
    src = {"video": os.path.basename(video),
           "size": os.path.getsize(video),
           "mtime": int(os.path.getmtime(video))}
    man = os.path.join(fdir, ".source.json")
    old = None
    if os.path.isfile(man):
        try:
            with open(man, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = None
    stale = [f for f in os.listdir(fdir) if f.endswith(".png")]
    if stale and old != src:
        why = "源视频变了" if old else "没有身份文件的旧帧（归属未知）"
        log(f"作废旧帧 {len(stale)} 张（{why}）")
        for f in stale:
            try:
                os.remove(os.path.join(fdir, f))
            except OSError as e:
                log(f"  删不掉 {f}：{e}——请手工清空 {fdir} 再跑")
                return 1
    if a.at:
        times = [float(x) for x in a.at.split(",")]
    elif a.every:
        rc, dout, derr = run_to_files(
            [FP, "-v", "error",
             "-show_entries", "format=duration", "-of", "csv=p=0", video])
        try:
            dur = float(dout.decode("utf-8", "ignore").strip())
        except ValueError:
            dur = 0.0
        # 读不到时长必须报错退出。以前这里把失败降级成 0，range(0, 0+1) 正好给出
        # 1 帧——「抽帧成功」但素材只有一张，下游全都照常，没有任何一处会报错。
        if rc != 0 or dur <= 0:
            log("ffprobe 读不到时长（rc=%d，ffprobe=%s）" % (rc, FP))
            detail = (derr or dout).decode("utf-8", "ignore").strip()
            if detail:
                log("  它说：" + detail[:200])
            log("  拒绝用「时长 0」继续——那会静默只抽 1 帧。")
            log("  修法：确认 ffprobe 可用（设 COURSE_FFPROBE，或装进 PATH）；"
                "或用 --at t1,t2,... 显式给时刻。")
            return 1
        # 末尾那一点不算：t == dur 已经越过最后一行，ffmpeg 会「成功」但不产出文件。
        times = [float(t) for t in range(0, int(dur) + 1, a.every) if t < dur]
    else:
        log("要给 --every N 或 --at t1,t2,...")
        return 1
    log(f"抽 {len(times)} 帧（-ss 精确 seek）")
    failed, written = [], 0
    for n, t in enumerate(times, 1):
        out = os.path.join(fdir, "f_%04d.png" % int(t))
        if _png_ok(out):
            continue
        # 产物判据：rc=0 不等于出了文件。ffmpeg 越过末尾时会「成功」但什么都不写，
        # 所以这里必须查文件本身（阈值与上面跳过已抽帧的一致）。
        if not run_ff(["-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", out]) \
                or not _png_ok(out):
            failed.append(int(t))
        else:
            written += 1
        if n % 20 == 0:
            log(f"  {n}/{len(times)}")
    if failed:
        log("有 %d 帧没抽出来（t=%s）——不许当成功。"
            % (len(failed), ",".join(str(t) for t in failed[:10])))
        return 1
    # 收尾：身份对上了才准写身份证、才准报就绪。
    # 这是模型验证过的唯一安全路径——「报成功」这一步本身必须带条件。
    with open(man, "w", encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False)
    have = len([f for f in os.listdir(fdir) if f.endswith(".png")])
    expect = len({int(t) for t in times})
    log(f"帧就绪: {have} 张（本次新抽 {written} 张）")
    if have < expect:
        log("  目录里只有 %d 张，少于该有的 %d 张——不许当成功。" % (have, expect))
        return 1
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
