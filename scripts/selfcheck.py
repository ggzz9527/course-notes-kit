#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""selfcheck.py —— 装完先跑这个，确认工具链在这台机器上真的能用

不联网、不下载、不改任何文件；只检查本机能力与脚本可导入性，
并给出明确的「缺什么、怎么补」。

用法：
  python scripts/selfcheck.py
退出码：0 全通过（允许 warn）；1 有 FAIL。
"""
import os
import shutil
import subprocess
import sys

# Windows 控制台默认 GBK，中文输出会乱码；统一转 UTF-8（失败就算了，不影响逻辑）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")

FAILS, WARNS, OKS = [], [], []


def ok(msg):
    OKS.append(msg)
    print("  [ OK ] %s" % msg)


def warn(msg):
    WARNS.append(msg)
    print("  [warn] %s" % msg)


def fail(msg):
    FAILS.append(msg)
    print("  [FAIL] %s" % msg)


def head(t):
    print("\n== %s" % t)


head("1. Python 与依赖")
print("  python: %s" % sys.version.split()[0])
if sys.version_info < (3, 10):
    fail("Python 需要 3.10+（当前 %s）" % sys.version.split()[0])
else:
    ok("Python 版本可用")

for mod, hint in (("yt_dlp", "pip install yt-dlp"),):
    try:
        __import__(mod)
        ok("已装 %s" % mod)
    except ImportError:
        fail("缺 %s —— %s" % (mod, hint))

try:
    import faster_whisper  # noqa: F401
    ok("已装 faster-whisper（可本地转写）")
    try:
        import av
        v = getattr(av, "__version__", "?")
        if v.startswith("13."):
            ok("av 版本 %s（符合要求）" % v)
        else:
            fail("av==%s —— 必须是 13.1.0，>=15 会让 faster-whisper TypeError" % v)
    except ImportError:
        fail("缺 av —— pip install \"av==13.1.0\"")
except ImportError:
    warn("未装 faster-whisper：只能吃外部字幕（T1/T2 降级路径），不是错误")

head("2. ffmpeg / ffprobe")
ff = os.environ.get("COURSE_FFMPEG") or os.path.join(TOOLS, "bin", "ffmpeg.exe")
fp = os.environ.get("COURSE_FFPROBE") or os.path.join(TOOLS, "bin", "ffprobe.exe")
for name, path, cmd in (("ffmpeg", ff, "ffmpeg"), ("ffprobe", fp, "ffprobe")):
    if os.path.exists(path):
        ok("%s: %s" % (name, path))
    elif shutil.which(cmd):
        ok("%s: PATH 里的 %s" % (name, shutil.which(cmd)))
    else:
        fail("找不到 %s —— 放 tools/bin/ 或设 COURSE_FFMPEG / 装进 PATH" % name)

head("3. 脚本可解析")

# 同进程 compile() 就够：不起子进程、不写临时文件
# （沙箱里 subprocess 管道和临时目录都可能被拦，这条坑本仓库自己踩过）
bad = 0
pyfiles = sorted(f for f in os.listdir(TOOLS) if f.endswith(".py"))
for f in pyfiles:
    p = os.path.join(TOOLS, f)
    try:
        with open(p, encoding="utf-8") as fh:
            compile(fh.read(), p, "exec")
    except SyntaxError as e:
        bad += 1
        fail("语法错误：%s 第 %s 行：%s" % (f, e.lineno, e.msg))
if not bad:
    ok("%d 个脚本语法正常" % len(pyfiles))

head("4. 目录与环境变量")
out = os.environ.get("COURSE_OUT") or os.path.join(ROOT, "course-notes")
media = os.environ.get("COURSE_MEDIA") or os.path.join(TOOLS, "media", "course")
print("  COURSE_OUT   = %s %s" % (out, "(已存在)" if os.path.isdir(out) else "(尚未创建)"))
print("  COURSE_MEDIA = %s %s" % (media, "(已存在)" if os.path.isdir(media) else "(尚未创建)"))

head("5. 模型配置（可选）")
cj = os.path.join(TOOLS, "llm.json")
if os.path.exists(cj):
    import json
    try:
        c = json.load(open(cj, encoding="utf-8"))
        miss = [k for k in ("base_url", "api_key", "model") if not c.get(k)]
        if miss:
            warn("tools/llm.json 缺字段：%s（只影响 --llm 摘要）" % ", ".join(miss))
        else:
            ok("tools/llm.json 已配置（model=%s）" % c.get("model"))
    except Exception as e:
        fail("tools/llm.json 不是合法 JSON：%s" % e)
else:
    warn("没有 tools/llm.json —— 只影响 course_digest.py --llm；"
         "复制 tools/llm.example.json 再填你自己的")

head("6. 课程画像")
prof = os.path.join(out, "00-课程画像.md")
if os.path.exists(prof):
    ok("已有课程画像：%s" % prof)
else:
    warn("还没有课程画像 —— 开工前先跑 course_profile.py，否则讲义没有骨架依据")

print("\n" + "=" * 46)
print("通过 %d 项 / 警告 %d 项 / 失败 %d 项" % (len(OKS), len(WARNS), len(FAILS)))
if FAILS:
    print("\n先修掉这些再用：")
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("可以开工。先跑 course_profile.py，再跑 course_run.py fetch。")
