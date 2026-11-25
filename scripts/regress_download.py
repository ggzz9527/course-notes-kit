#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""regress_download.py —— 下载「产物完整性」回归（6 个场景，全自动，不联网）

为什么不塞进 selfcheck.py：selfcheck 的契约写明「不联网、不下载、**不改任何文件**」，
而下载回归必须真写文件。塞进去就破了它的契约，下一个会话会不敢跑 selfcheck。

它守的这条判据已经过状态机验证（logicprobe，不变量 no-false-done /
done-needs-verify / done-no-residue / always-terminates）：
    不存在「报了成功但落盘文件是残的」的可达状态
反例是上一轮被打回的那一版——只在下载时守（分片长度），而 `ffmpeg -c copy`
对截断流**可能 rc=0**，于是三步就能「报成功但文件是残的」：
    INIT --start--> DOWNLOAD --dl_ok--> REMUX --ff_corrupt--> DONE
所以判据必须落在**成品**上：每次成功都要 ffprobe 读一次时长、与 meta.json 的
duration 比对（容差 1 s），并且与上次核过的字节数一致（`<dest>.id.json`）。
体积猜不出来：真实 720P 与低码率集差一个数量级，截断件也能「够大」。

判据必须能失败：下面每个 FAIL 场景都对应一种真实失效——
206 少给一段、Content-Length 不符、mirror 全挂、全挂、残品冒充完整、残渣继承。

为什么全用本地伪造响应（http.server）：真去连 B 站，判据会随对方抽风而飘，
换个节点就从 6/0 变成 3/3，那种回归比没有还坏。

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
os.environ["COURSE_MEDIA"] = os.path.join(WORK, "media")
os.environ["COURSE_TMP"] = os.path.join(WORK, "tmp")
os.makedirs(os.environ["COURSE_MEDIA"], exist_ok=True)
os.makedirs(os.environ["COURSE_TMP"], exist_ok=True)

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
    """
    body = b""
    mode = "ok"
    hits = 0

    def log_message(self, *a):          # 别把访问日志刷进判据输出
        pass

    def do_GET(self):
        type(self).hits += 1
        total = len(Stub.body)
        rng = self.headers.get("Range")
        if Stub.mode == "badcl":
            self.send_response(200)
            self.send_header("Content-Length", str(total + 5000))
            self.end_headers()
            self.wfile.write(Stub.body)
            self.close_connection = True
            return
        if rng:
            s, e = _parse_range(rng, total)
            data = Stub.body[s:e + 1]
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
            self.wfile.write(Stub.body)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


SRC = ""        # 本地造的真 m4a，当作源站 body
WANT = 0.0      # 它的真实时长，等于 meta.duration
STUB_URL = ""
DEAD_URL = ""


def run_audio():
    """跑一次 course_prep 的 audio 子命令（同进程，不走 CLI）。

    fetch_meta 必须挡掉：它背后是 yt-dlp 连 B 站。这里换成固定 meta，
    判据只考下载/核验这一段，不考「能不能取到 meta」。
    """
    buf = io.StringIO()

    class NS:
        bv, ep, force = BV, EP, False

    with contextlib.redirect_stdout(buf):
        rc = _prep.cmd_audio(NS)
    return rc, buf.getvalue()


def fresh_dir():
    d = _prep.media_dir(BV, EP)
    shutil.rmtree(d, ignore_errors=True)
    return _prep.media_dir(BV, EP)


def parts_left(d):
    return [n for n in os.listdir(d)
            if ".part" in n or n.startswith("_")]


def main():
    global SRC, WANT, STUB_URL, DEAD_URL
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(os.environ["COURSE_MEDIA"], exist_ok=True)
    os.makedirs(os.environ["COURSE_TMP"], exist_ok=True)
    print("== 下载产物完整性回归（临时目录 %s）" % WORK)

    # 造一个真 m4a 当源站内容；时长从 ffprobe 读回来，不写死
    SRC = os.path.join(WORK, "src.m4a")
    p = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=%d" % DUR,
                        "-c:a", "aac", "-b:a", "32k", "-y", SRC],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if p.returncode != 0 or not os.path.isfile(SRC):
        raise SystemExit("× 造测试音频失败（ffmpeg rc=%d）" % p.returncode)
    with open(SRC, "rb") as f:
        Stub.body = f.read()
    WANT = _prep.probe_duration(SRC)
    if not WANT:
        raise SystemExit("× ffprobe 读不出测试音频时长")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    STUB_URL = base + "/media.m4s"
    DEAD_URL = "http://127.0.0.1:%d/media.m4s" % free_port()

    # mirror 一律指向死端口：既保证「mirror 全挂 → 回退原始直链」这条路每个场景都走一遍，
    # 也保证这个脚本绝不会真的去连 upos-*.bilivideo.com。
    _prep.MIRRORS = ["127.0.0.1:%d" % free_port()]
    _prep.fetch_meta = lambda bv, ep, force=False: {
        "duration": WANT, "title": "regress", "_at": 0,
        "formats": [{"format_id": "30216", "url": STUB_URL,
                     "vcodec": "none", "filesize": len(Stub.body)}]}

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
