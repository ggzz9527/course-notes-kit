#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""regress_frames.py —— 抽帧「产物归属」回归（6 个场景，全自动，自建临时目录）

为什么不塞进 selfcheck.py：selfcheck 的契约写明「不联网、不下载、**不改任何文件**」，
而抽帧回归必须真写文件。塞进去就破了它的契约，下一个会话会不敢跑 selfcheck。

它守的这条判据已经过状态机验证（logicprobe，不变量 never-states: DONE_STALE）：
    「不存在『报就绪但帧属于别的视频』的可达状态」
判据只能这样验：同一个 frames 目录，先抽 A，再把视频换成 B，看它还说不说「就绪」。
换句话说——**判据必须能失败**：下面每个 FAIL 场景都对应一个曾经真实发生过的谎报。

用法：
  python scripts/regress_frames.py
退出码：0 全过；1 有场景没过。
"""
import io
import importlib.util
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PREP = os.path.join(ROOT, "tools", "course_prep.py")
WORK = os.path.join(ROOT, "tools", "tmp", "_regress_frames")

# 复用 course_prep 自己解析出来的 ffmpeg 路径，别在这里再造一份「工具在哪」
_spec = importlib.util.spec_from_file_location("course_prep", PREP)
_prep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prep)
FFMPEG = _prep.FF

RESULTS = []


def ok(name, detail=""):
    RESULTS.append((True, name, detail))
    print("  [ OK ] %-42s %s" % (name, detail))


def bad(name, detail=""):
    RESULTS.append((False, name, detail))
    print("  [FAIL] %-42s %s" % (name, detail))


def run_frames(d, env_extra=None):
    """跑 frames 子命令。沙箱禁命名管道，输出必须重定向到文件再读回。"""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    log = os.path.join(WORK, "_run.log")
    with open(log, "wb") as f:
        rc = subprocess.call([sys.executable, PREP, "frames", d, "--every", "1"],
                             stdout=f, stderr=subprocess.STDOUT, env=env)
    return rc, io.open(log, encoding="utf-8", errors="replace").read()


def make_video(p, dur, kind="testsrc"):
    lavfi = ("testsrc=size=320x240:rate=10:duration=%d" % dur if kind == "testsrc"
             else "color=red:size=320x240:rate=10:duration=%d" % dur)
    log = os.path.join(WORK, "_ff.log")
    with open(log, "wb") as f:
        rc = subprocess.call([FFMPEG, "-v", "error", "-f", "lavfi", "-i", lavfi,
                              "-pix_fmt", "yuv420p", "-y", p],
                             stdout=f, stderr=subprocess.STDOUT)
    if rc != 0:
        raise SystemExit("× 造测试视频失败（ffmpeg rc=%d），先看 %s" % (rc, log))


def pngs(d):
    fdir = os.path.join(d, "frames")
    if not os.path.isdir(fdir):
        return []
    return sorted(x for x in os.listdir(fdir) if x.endswith(".png"))


def main():
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    d = os.path.join(WORK, "ep")
    os.makedirs(d)
    video = os.path.join(d, "video.mp4")
    print("== 抽帧产物归属回归（临时目录 %s）" % WORK)

    # A 正向：3 秒视频 --every 1 → 3 帧
    make_video(video, 3)
    rc, out = run_frames(d)
    n = len(pngs(d))
    (ok if rc == 0 and n == 3 else bad)("A 3s → 3 帧", "rc=%d 帧数=%d" % (rc, n))

    # B 换短视频：必须作废旧帧，不许拿 3 张冒充 1 张
    make_video(video, 1)
    rc, out = run_frames(d)
    n = len(pngs(d))
    (ok if rc == 0 and n == 1 else bad)("B 换 1s 视频 → 1 帧（不放任旧帧）",
                                        "rc=%d 帧数=%d" % (rc, n))

    # C 同长度不同画面：必须重抽（否则讲义配上别集的截图，且处处报成功）
    make_video(video, 3, "testsrc")
    rc, _ = run_frames(d)
    before = open(os.path.join(d, "frames", "f_0000.png"), "rb").read()
    make_video(video, 3, "red")
    rc, out = run_frames(d)
    after = open(os.path.join(d, "frames", "f_0000.png"), "rb").read()
    (ok if rc == 0 and len(pngs(d)) == 3 and before != after else bad)(
        "C 同 3s 换画面 → 真重抽", "rc=%d 变了=%s" % (rc, before != after))

    # D 幂等：源没变就不该重抽
    rc, out = run_frames(d)
    (ok if rc == 0 and "本次新抽 0 张" in out and len(pngs(d)) == 3 else bad)(
        "D 重复跑 → 新抽 0 张", "rc=%d" % rc)

    # E 老目录：有 png 没身份证 → 归属未知，作废重抽
    man = os.path.join(d, "frames", ".source.json")
    if os.path.isfile(man):
        os.remove(man)
    rc, out = run_frames(d)
    (ok if rc == 0 and "作废旧帧" in out and os.path.isfile(man) else bad)(
        "E 无身份证的旧帧 → 作废重抽", "rc=%d" % rc)

    # F 负向：ffprobe 不存在 → exit 1 且给一句修法（不许 traceback）
    shutil.rmtree(os.path.join(d, "frames"), ignore_errors=True)
    rc, out = run_frames(d, {"COURSE_FFPROBE": os.path.join(WORK, "nope", "ffprobe.exe")})
    good = rc == 1 and "修法" in out and "Traceback" not in out
    (ok if good else bad)("F ffprobe 缺失 → exit 1 + 修法", "rc=%d" % rc)

    nbad = sum(1 for r in RESULTS if not r[0])
    print("\n" + "=" * 46)
    print("通过 %d 项 / 失败 %d 项" % (len(RESULTS) - nbad, nbad))
    if nbad == 0:
        print("抽帧产物归属正常。")
        return 0
    print("有 FAIL —— 抽帧又开始「说成功但素材不对」，改完再发版。")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
