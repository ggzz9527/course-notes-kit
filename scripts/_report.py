#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归/自检脚本共用的输出契约：`--json` + 自证「跑的是哪棵树」。

为什么单独一个文件（而 stub 不抽）：三个脚本的 JSON **必须是同一份契约**——
模型解析结果时不该为了换脚本就换键名，契约一旦有两份就会分叉。
而 stub 依赖的是**被测代码**，抽早了只是多一层间接；输出契约依赖的是**调用方**，
本来就该唯一。判据：新增的东西有没有「第二个使用者」——有就抽，没有就等。

三态：`ok` / `warn` / `fail`。**警告不算失败**（selfcheck 的正常态就是带一条警告），
所以每条结果的 `ok` 字段含义是「没失败」，三态本身看 `status`。

为什么不调 `git rev-parse`：沙箱禁命名管道，`subprocess` 抓输出会 PermissionError；
直接读 `.git/HEAD` 既不依赖 git 二进制，也没有管道。读不到就返回 "unknown"——
自证是给人/模型看的描述，不是闸门（zip 分发的包本来就没有 .git）。

用法（脚本侧）：
    import _report as R
    ok, warn, bad = R.ok, R.warn, R.bad       # 场景里照常 ok(name, detail)
    R.banner("== ...")                        # JSON 模式走 stderr，不污染 stdout
    return R.finish(SCRIPT, ROOT, "全过。", "有 FAIL——改完再发版。")

命令行：`python scripts/<脚本>.py --json`
stdout 只有一个 JSON 对象：
    {"ok": true, "script": "...", "root": "...", "rev": "e1a91ea",
     "total": 8, "failed": 0, "warnings": 1,
     "results": [{"status": "ok", "ok": true, "name": "...", "detail": "..."}, ...]}
崩了也照样给结论：`{"ok": false, ..., "error": "TypeError: ..."}`。
退出码不变（0 无失败 / 1 有失败），所以 `--json` 是**加信息不减信息**。
"""
import json
import os
import sys

JSON_MODE = "--json" in sys.argv[1:]
RESULTS = []          # [(status, name, detail)]，status ∈ {"ok", "warn", "fail"}


def git_rev(root):
    """读 .git 拿短 rev；worktree（.git 是文件）也认；读不到返回 "unknown"。"""
    gitdir = os.path.join(root, ".git")
    try:
        if os.path.isfile(gitdir):
            with open(gitdir, encoding="utf-8") as f:
                line = f.read().strip()
            if line.startswith("gitdir:"):
                gitdir = os.path.normpath(
                    os.path.join(root, line.split(":", 1)[1].strip()))
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            with open(os.path.join(gitdir, ref), encoding="utf-8") as f:
                head = f.read().strip()
        return (head or "unknown")[:7]
    except OSError:
        return "unknown"


def _line(s):
    out = sys.stderr if JSON_MODE else sys.stdout
    out.write(s + "\n")
    out.flush()


def _emit(status, name, detail=""):
    RESULTS.append((status, name, detail))
    tag = {"ok": "[ OK ]", "warn": "[warn]", "fail": "[FAIL]"}[status]
    # 没有 detail 时不补宽度：selfcheck 那种整句就是名字，补了只留一串尾随空格
    _line("  %s %-46s %s" % (tag, name, detail) if detail
          else "  %s %s" % (tag, name))


def ok(name, detail=""):
    _emit("ok", name, detail)


def warn(name, detail=""):
    _emit("warn", name, detail)


def bad(name, detail=""):
    _emit("fail", name, detail)


def failures():
    return [r[1] for r in RESULTS if r[0] == "fail"]


def banner(text):
    _line(text)


def summary(script, root):
    nfail = sum(1 for r in RESULTS if r[0] == "fail")
    nwarn = sum(1 for r in RESULTS if r[0] == "warn")
    return {"ok": nfail == 0, "script": script, "root": root, "rev": git_rev(root),
            "total": len(RESULTS), "failed": nfail, "warnings": nwarn,
            "results": [{"status": r[0], "ok": r[0] != "fail",
                         "name": r[1], "detail": r[2]} for r in RESULTS]}


def emit_error(script, root, err):
    """崩了也要给结论：JSON 模式输出一个 ok:false，别让人去猜 traceback。"""
    if not JSON_MODE:
        return
    sys.stdout.write(json.dumps(
        {"ok": False, "script": script, "root": root, "rev": git_rev(root),
         "error": err}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def finish(script, root, ok_line, fail_line, sep=50, cols="of"):
    """打印计数并给出退出码。cols="owf" 才带警告列（selfcheck 的正常态就带一条警告）。"""
    nfail = sum(1 for r in RESULTS if r[0] == "fail")
    nwarn = sum(1 for r in RESULTS if r[0] == "warn")
    if JSON_MODE:
        sys.stdout.write(json.dumps(summary(script, root), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        print("\n" + "=" * sep)
        if cols == "owf":
            print("通过 %d 项 / 警告 %d 项 / 失败 %d 项"
                  % (len(RESULTS) - nfail - nwarn, nwarn, nfail))
        else:
            print("通过 %d 项 / 失败 %d 项" % (len(RESULTS) - nfail, nfail))
        msg = ok_line if nfail == 0 else fail_line
        if msg:
            print(msg)
    return 0 if nfail == 0 else 1
