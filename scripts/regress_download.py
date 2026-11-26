#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""regress_download.py —— 下载「产物完整性」回归（8 个场景，全自动，不联网）

为什么不塞进 selfcheck.py：selfcheck 的契约写明「不联网、不下载、**不改任何文件**」，
而下载回归必须真写文件。塞进去就破了它的契约，下一个会话会不敢跑 selfcheck。

它守的这条判据已经过状态机验证（logicprobe，不变量 no-false-done /
done-needs-verify / done-no-residue / always-terminates）：
    不存在「报了成功但落盘文件是残的」的可达状态
反例是上一轮被打回的那一版——只在下载时守（分片长度），而 `ffmpeg -c copy`
对截断流**可能 rc=0**，于是三步就能「报成功但文件是残的」：
    INIT →(start)→ DOWNLOAD →(dl_ok)→ REMUX →(ff_corrupt)→ DONE
所以判据必须落在**成品**上：每次成功都要 ffprobe 读一次时长、与 meta.json 的
duration 比对（容差 1 s），并且与上次核过的字节数一致（`<dest>.id.json`）。
体积猜不出来：真实 720P 与低码率集差一个数量级，截断件也能「够大」。

判据必须能失败：下面每个 FAIL 场景都对应一种真实失效——
206 少给一段、Content-Length 不符、mirror 全挂、全挂、残品冒充完整、残渣继承、
`--keep` 拿整集当完成（老代码遇到大文件直接 return 0，一个片段都不产）、
`--keep` 继承残整集。

为什么全用本地伪造响应（http.server）：真去连 B 站，判据会随对方抽风而飘，
换个节点就从全绿变成一堆红，那种回归比没有还坏。

用法：
  python scripts/regress_download.py
退出码：0 全过；1 有场景没过。
"""
import contextlib
import http.server
import importlib.util
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PREP = os.path.join(ROOT, "tools", "course_prep.py")
WORK = os.path.join(ROOT, "tools", "tmp", "_regress_download")
BV, EP = "BV1regressdl", 1
DUR = 6            # 测试音频时长（秒）
MODE = "ok"        # 当前伪造响应模式

# COURSE_MEDIA / COURSE_TMP 必须在 import course_prep 之前设好——它在模块级读环境变量，
# 晚一步就会往真实素材目录里写测试文件（那是不可接受的副作用）。
def _ensure_dir(p, tries=8):
    """建目录，带重试。

    2026-11-26 冷启动实测：脚本每次跑完会 rmtree 掉自己的临时目录，而 Windows 上
    刚删的目录会短暂处于 delete-pending（杀软/索引还持有句柄），紧接着重建就报
    WinError 5。那是环境抖动，不是缺陷——判据该给出结论，不该抛 traceback。
    """
    import time as _t
    last = None
    for i in range(tries):
        try:
            os.makedirs(p, exist_ok=True)
            return
        except OSError as e:
            last = e
            _t.sleep(0.15 * (i + 1))
    sys.exit("× 建不了临时目录 %s：%s\n"
             "  修法：把临时目录挪开（换个非 Temp 的克隆位置），或等几秒重跑。"
             % (p, last))


os.environ["COURSE_MEDIA"] = os.path.join(WORK, "media")
os.environ["COURSE_TMP"] = os.path.join(WORK, "tmp")
_ensure_dir(os.environ["COURSE_MEDIA"])
_ensure_dir(os.environ["COURSE_TMP"])

_spec = importlib.util.spec_from_file_location("course_prep", PREP)
_prep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prep)
FFMPEG, FFPROBE = _prep.FF, _prep.FP

RESULTS = []


def ok(name, detail=""):
    RESULTS.append((True, name, detail))
    print("  [ OK ] %-46s %s" % (name, detail))


def bad(name, detail=""):
    RESULTS.append((False, name, detail))
    print("  [FAIL] %-46s %s" % (name, detail))


def _parse_range(v, total):
    m = re.match(r"bytes=(\d+)-(\d*)", v or "")
    if not m:
        return 0, total - 1
    s = int(m.group(1))
    e = int(m.group(2)) if m.group(2) else total - 1
    return s, min(e, total - 1)


class Stub(http.server.BaseHTTPRequestHandler):
    """可指定状态码/分段长度/Content-Length 的假源站。

    ok       —— 206 分段，Content-Range 与 body 一致
    short    —— 206，Content-Range 声明的字节数 > 实际 body（少给一段）
    badcl    —— 不支持 Range（回 200），Content-Length 比实际 body 大

    按路径分发：`/media.m4s` 音频、`/video.m4s` 视频。--keep 那两个场景要音视频
    都能下，否则测不到合并那一步。
    """
    bodies = {}
    mode = "ok"
    hits = 0

    def log_message(self, *a):          # 别把访问日志刷进判据输出
        pass

    def do_GET(self):
        type(self).hits += 1
        body = Stub.bodies.get(self.path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        total = len(body)
        rng = self.headers.get("Range")
        if Stub.mode == "badcl":
            self.send_response(200)
            self.send_header("Content-Length", str(total + 5000))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        if rng:
            s, e = _parse_range(rng, total)
            data = body[s:e + 1]
            if Stub.mode == "short":
                data = data[:max(1, len(data) // 2)]
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (s, e, total))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(total))
            self.end_headers()
            self.wfile.write(body)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


SRC = ""        # 本地造的真 m4a，当作源站 body
WANT = 0.0      # 它的真实时长，等于 meta.duration
STUB_URL = ""
STUB_VURL = ""
DEAD_URL = ""
META = {}


def quiet(fn, *a, **kw):
    """跑一个会打日志的函数，把输出收走；返回 (结果, 输出)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


def run_audio():
    """跑一次 course_prep 的 audio 子命令（同进程，不走 CLI）。

    fetch_meta 必须挡掉：它背后是 yt-dlp 连 B 站。这里换成固定 meta，
    判据只考下载/核验这一段，不考「能不能取到 meta」。
    """

    class NS:
        bv, ep, force = BV, EP, False

    return quiet(_prep.cmd_audio, NS)


def run_video(keep=None):
    """跑一次 video 子命令；keep=None 是整集模式，'0-2' 是 --keep 模式。"""
    ns = type("NS", (), {})()
    ns.bv, ns.ep, ns.force, ns.quality, ns.keep = BV, EP, False, 720, keep
    return quiet(_prep.cmd_video, ns)


def fresh_dir():
    d = _prep.media_dir(BV, EP)
    shutil.rmtree(d, ignore_errors=True)
    return _prep.media_dir(BV, EP)


def parts_left(d):
    return [n for n in os.listdir(d)
            if ".part" in n or n.startswith("_")]


def _tail(text, n=1):
    """取日志最后 n 行非空内容——失败时把原因带进判据输出，别只报一个 rc。"""
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    return "｜".join(lines[-n:]) if lines else ""


def main():
    global SRC, WANT, STUB_URL, STUB_VURL, DEAD_URL
    shutil.rmtree(WORK, ignore_errors=True)
    _ensure_dir(os.environ["COURSE_MEDIA"])
    _ensure_dir(os.environ["COURSE_TMP"])
    print("== 下载产物完整性回归（临时目录 %s）" % WORK)

    # 造真音频 + 真视频当源站内容；时长从 ffprobe 读回来，不写死
    SRC = os.path.join(WORK, "src.m4a")
    VID = os.path.join(WORK, "src.mp4")
    for path, args, what in (
        (SRC, ["-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % DUR,
               "-c:a", "aac", "-b:a", "32k"], "音频"),
        (VID, ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=%d" % DUR,
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
         "视频"),
    ):
        p = subprocess.run([FFMPEG, "-v", "error"] + args + ["-y", path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if p.returncode != 0 or not os.path.isfile(path):
            raise SystemExit("× 造测试%s失败（ffmpeg rc=%d）" % (what, p.returncode))
    with open(SRC, "rb") as f:
        Stub.bodies["/media.m4s"] = f.read()
    with open(VID, "rb") as f:
        Stub.bodies["/video.m4s"] = f.read()
    WANT = _prep.probe_duration(SRC)
    if not WANT:
        raise SystemExit("× ffprobe 读不出测试音频时长")
    vdur = _prep.probe_duration(VID)
    if not vdur or abs(vdur - WANT) > 0.2:
        # 音视频时长对不上，--keep 那两个场景就分不清是「合并错了」还是「下载错了」
        raise SystemExit("× 测试音视频时长对不上（%.2f vs %.2f）" % (WANT, vdur or 0))

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    STUB_URL = base + "/media.m4s"
    STUB_VURL = base + "/video.m4s"
    DEAD_URL = "http://127.0.0.1:%d/media.m4s" % free_port()

    # mirror 一律指向死端口：既保证「mirror 全挂 → 回退原始直链」这条路每个场景都走一遍，
    # 也保证这个脚本绝不会真的去连 upos-*.bilivideo.com。
    _prep.MIRRORS = ["127.0.0.1:%d" % free_port()]
    META.update({
        "duration": WANT, "title": "regress", "_at": 0,
        "formats": [
            {"format_id": "30216", "url": STUB_URL, "vcodec": "none",
             "filesize": len(Stub.bodies["/media.m4s"])},
            {"format_id": "30064", "url": STUB_VURL, "vcodec": "avc1",
             "height": 720, "filesize": len(Stub.bodies["/video.m4s"])},
        ]})
    _prep.fetch_meta = lambda bv, ep, force=False: META

    try:
        # A 206 分段少给一段：body 比 Content-Range 短 → 必须失败，不许拼出「看起来完整」
        #   判据钉在 download() 自己的守上（日志点名「字节数不符」），
        #   否则这条会被「ffmpeg 恰好合不了截断流」兜住而假绿——那样测不到新增的逐段核对。
        Stub.mode, Stub.hits = "short", 0
        d = fresh_dir()
        rc, out = run_audio()
        left = parts_left(d)
        (ok if rc == 1 and "字节数不符" in out
         and not os.path.isfile(os.path.join(d, "audio.m4a")) and not left
         else bad)("A 206 少给一段 → exit 1、不落盘",
                   "rc=%d 逐段核对=%s 残渣=%s"
                   % (rc, "字节数不符" in out, left or "无"))

        # B Content-Length 与实际 body 不符 → exit 1
        Stub.mode, Stub.hits = "badcl", 0
        d = fresh_dir()
        rc, out = run_audio()
        (ok if rc == 1 and "字节数不符" in out
         and not os.path.isfile(os.path.join(d, "audio.m4a"))
         else bad)("B Content-Length 不符 → exit 1",
                   "rc=%d 逐段核对=%s" % (rc, "字节数不符" in out))

        # C 所有 mirror 403/超时，原始直链仍可用 → 回退成功（不是报成功却 0 字节）
        Stub.mode, Stub.hits = "ok", 0
        d = fresh_dir()
        rc, out = run_audio()
        dest = os.path.join(d, "audio.m4a")
        good, why = _prep.verify_artifact(dest, WANT)
        (ok if rc == 0 and good and Stub.hits > 0 else bad)(
            "C mirror 全挂 → 回退原始直链 exit 0",
            "rc=%d %s" % (rc, why))

        # D 所有 mirror 与原始直链全挂 → exit 1 + 一句修法（不是 traceback）
        keep_fmt = _prep.pick_format
        _prep.pick_format = lambda meta, kind, quality=720: {
            "format_id": "30216", "url": DEAD_URL, "vcodec": "none"}
        d = fresh_dir()
        rc, out = run_audio()
        _prep.pick_format = keep_fmt
        (ok if rc == 1 and "修法" in out and "Traceback" not in out else bad)(
            "D 全挂 → exit 1 + 一句修法",
            "rc=%d 有修法=%s" % (rc, "修法" in out))

        # E 已存在完整文件，再跑一次 → 一次 HTTP 都不发（幂等），exit 0
        Stub.mode, Stub.hits = "ok", 0
        d = fresh_dir()
        dest = os.path.join(d, "audio.m4a")
        rc1, _ = run_audio()               # 第一次：真下
        ready = rc1 == 0 and os.path.isfile(dest)
        if ready:
            Stub.hits = 0
            rc, out = run_audio()          # 第二次：应直接跳过
            (ok if rc == 0 and Stub.hits == 0 else bad)(
                "E 完整文件再跑 → 不重下 exit 0",
                "rc=%d 请求数=%d" % (rc, Stub.hits))
        else:
            # 判据要能在「实现坏掉」时报 FAIL，而不是让下一场景 getssize 崩掉
            bad("E 完整文件再跑 → 不重下 exit 0", "第一次下载就没成功 rc=%d" % rc1)

        # F 文件存在但上次下到一半（截断 + 残渣）→ 识别为残，重下；不许继承
        #   残渣的名字必须是真实中间产物那一族（_a.m4s.part），
        #   随手拿 audio.m4a.part 试是测不到 download() 的清场逻辑的。
        Stub.mode, Stub.hits = "ok", 0
        if not ready:
            bad("F 半截文件 → 识别为残重下", "依赖 E 的产物，E 已失败")
        else:
            full_size = os.path.getsize(dest)
            with open(dest, "rb") as f:
                half = f.read(full_size // 2)
            with open(dest, "wb") as f:       # 就地截断，模拟「下载之后被截断/替换」
                f.write(half)
            for junk in ("_a.m4s.part", "_a.m4s.part.0", "_a.m4s.part.7"):
                # .7 是关键：workers=4 时本次只会写 0..3，上一轮若用过更多分片，
                # 残留的 .4..N 必须被清掉，否则会被当成本次结果拼进去。
                with open(os.path.join(d, junk), "wb") as f:
                    f.write(b"\x00" * 1024)
            rc, out = run_audio()
            good, why = _prep.verify_artifact(dest, WANT)
            left = parts_left(d)
            (ok if rc == 0 and good and Stub.hits > 0
             and os.path.getsize(dest) == full_size and not left else bad)(
                "F 半截文件 → 识别为残重下",
                "rc=%d %s 大小复原=%s 残渣=%s"
                % (rc, why, os.path.getsize(dest) == full_size, left or "无"))

        # G --keep + 已有完整整集 → 只裁片段，不重下
        #   守的是「--keep 的产物是片段，不是整集」：老代码遇到大于阈值的 video.mp4
        #   直接 return 0，一个片段都不产，照样报成功——和 A/B 是同一类谎报，只是入口不同。
        Stub.mode, Stub.hits = "ok", 0
        d = fresh_dir()
        vdest = os.path.join(d, "video.mp4")
        (rc0, _vd), vlog = quiet(_prep._fetch_to, "video", d, META, WANT, 720)
        vready = rc0 == 0 and os.path.isfile(vdest)
        if vready:
            Stub.hits = 0
            rc, out = run_video("0-2")
            clip = os.path.join(d, "clip1_0-2.mp4")
            (ok if rc == 0 and Stub.hits == 0 and os.path.isfile(clip)
             and os.path.getsize(clip) > 0 and os.path.isfile(vdest) else bad)(
                "G --keep 已有整集 → 只裁不重下",
                "rc=%d 请求数=%d 出片段=%s" % (rc, Stub.hits, os.path.isfile(clip)))
        else:
            bad("G --keep 已有整集 → 只裁不重下",
                "整集没下成功 rc=%d｜%s" % (rc0, _tail(vlog)))

        # H --keep + 残整集 → 识别为残、重下、裁片段，且不把整集留在盘上
        #   （--keep 的意义就是别留整集；重下过的那份要清掉，但不能连片段一起清）
        Stub.mode, Stub.hits = "ok", 0
        if not vready:
            bad("H --keep 残整集 → 重下再裁", "依赖 G 的产物，G 已失败")
        else:
            d = fresh_dir()
            vdest = os.path.join(d, "video.mp4")
            (rc0, _vd), vlog = quiet(_prep._fetch_to, "video", d, META, WANT, 720)
            full = os.path.getsize(vdest) if os.path.isfile(vdest) else 0
            if rc0 != 0 or not full:
                bad("H --keep 残整集 → 重下再裁",
                    "整集没下成功 rc=%d｜%s" % (rc0, _tail(vlog)))
            else:
                with open(vdest, "rb") as f:
                    head = f.read(max(1024, full // 3))
                with open(vdest, "wb") as f:      # 就地截断成残整集
                    f.write(head)
                Stub.hits = 0
                rc, out = run_video("0-2")
                clip = os.path.join(d, "clip1_0-2.mp4")
                (ok if rc == 0 and Stub.hits > 0 and os.path.isfile(clip)
                 and os.path.getsize(clip) > 0 and not os.path.isfile(vdest) else bad)(
                    "H --keep 残整集 → 重下再裁",
                    "rc=%d 请求数=%d 出片段=%s 整集已清=%s"
                    % (rc, Stub.hits, os.path.isfile(clip),
                       not os.path.isfile(vdest)))
    finally:
        srv.shutdown()
        srv.server_close()

    nbad = sum(1 for r in RESULTS if not r[0])
    print("\n" + "=" * 50)
    print("通过 %d 项 / 失败 %d 项" % (len(RESULTS) - nbad, nbad))
    if nbad == 0:
        print("下载产物完整。")
        return 0
    print("有 FAIL —— 下载又开始「说成功但文件是残的」，改完再发版。")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
