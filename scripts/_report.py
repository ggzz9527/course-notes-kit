#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归脚本共用的输出契约：`--json` + 自证「跑的是哪棵树」。

为什么单独一个文件（而 stub 不抽）：两个回归脚本的 JSON **必须是同一份契约**——
模型解析结果时不该为了换脚本就换键名，契约一旦有两份就会分叉。
而 stub 依赖的是**被测代码**，抽早了只是多一层间接；输出契约依赖的是**调用方**，
本来就该唯一。判据：新增的东西有没有「第二个使用者」——有就抽，没有就等。

为什么不调 `git rev-parse`：沙箱禁命名管道，`subprocess` 抓输出会 PermissionError；
直接读 `.git/HEAD` 既不依赖 git 二进制，也没有管道。读不到就返回 "unknown"——
自证是给人/模型看的描述，不是闸门（zip 分发的包本来就没有 .git）。

用法（脚本侧）：
    import _report as R
    ok, bad = R.ok, R.bad           # 场景里照常 ok(name, detail)
    R.banner("== ...")              # JSON 模式走 stderr，不污染 stdout
    return R.finish(SCRIPT, ROOT, "全过。", "有 FAIL——改完再发版。")

命令行：`python scripts/regress_xxx.py --json`
stdout 只有一个 JSON 对象：
    {"ok": true, "script": "...", "root": "...", "rev": "e1a91ea",
     "total": 8, "failed": 0,
     "results": [{"ok": true, "name": "...", "detail": "..."}, ...]}
崩了也照样给结论：`{"ok": false, ..., "error": "TypeError: ..."}`。
退出码不变（0 全过 / 1 有失败），所以 `--json` 是**加信息不减信息**。
"""
import json
import os
import sys

JSON_MODE = "--json" in sys.argv[1:]
RESULTS = []


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


def ok(name, detail=""):
    RESULTS.append((True, name, detail))
    _line("  [ OK ] %-46s %s" % (name, detail))


def bad(name, detail=""):
    RESULTS.append((False, name, detail))
    _line("  [FAIL] %-46s %s" % (name, detail))


def banner(text):
    _line(text)


def summary(script, root):
    nbad = sum(1 for r in RESULTS if not r[0])
    return {"ok": nbad == 0, "script": script, "root": root, "rev": git_rev(root),
            "total": len(RESULTS), "failed": nbad,
            "results": [{"ok": r[0], "name": r[1], "detail": r[2]} for r in RESULTS]}


def emit_error(script, root, err):
    """崩了也要给结论：JSON 模式输出一个 ok:false，别让人去猜 traceback。"""
    if not JSON_MODE:
        return
    sys.stdout.write(json.dumps(
        {"ok": False, "script": script, "root": root, "rev": git_rev(root),
         "error": err}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def finish(script, root, ok_line, fail_line, sep=50):
    nbad = sum(1 for r in RESULTS if not r[0])
    if JSON_MODE:
        sys.stdout.write(json.dumps(summary(script, root), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        print("\n" + "=" * sep)
        print("通过 %d 项 / 失败 %d 项" % (len(RESULTS) - nbad, nbad))
        print(ok_line if nbad == 0 else fail_line)
    return 0 if nbad == 0 else 1
