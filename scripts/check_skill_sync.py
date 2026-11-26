#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_skill_sync.py —— skill 真源 ↔ 插件分发副本 的逐字闸门（无第三方依赖）

为什么需要它：同一个 skill 在仓库里有两份——`skills/course-selflearn/`（真源）和
`plugin/skills/course-selflearn/`（DSH 插件自带的分发副本，插件必须自包含）。
「我以为拷过了」是最常见的失效方式；副本错了时 skill 照样能装，只是内容悄悄旧了。

它查两类：

  1. **文件集 + 字节**：两边相对路径集合相同、逐个 sha256 相同。不比行数或大小——
     那两种都会放过「改了一个字」；
  2. **bundle 自洽**：`package.json` 的 type/exports/files/dsh.bundle.patch、patch 里
     `- insert:` 包裹、provider 名、`includeDefaultRoots: false`、`customSkillDirs`、
     解析的是本包 `<name>/package.json`，以及 SKILL.md 的 frontmatter name。

第 2 类不能省：第 1 类只证明「内容一样」，bundle 写错时副本一致却根本装不上——
副本闸门会给出假 PASS（实测踩过：patch 裸写 `- id:` 被当成覆盖 → warn+skip；
`!!js` 表达式写错同样静默）。

用法：
  python scripts/check_skill_sync.py           # 人类可读
  python scripts/check_skill_sync.py --json    # 机器读
退出码：0 一致；1 不一致；2 目录找不到。
"""
import hashlib
import io
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "skills", "course-selflearn")
PLUGIN = os.path.join(ROOT, "plugin")
PLUGIN_SKILL = os.path.join(PLUGIN, "skills", "course-selflearn")
PROVIDER = "@deepseek-ai/dsh-skill-filesystem"
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree(root):
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
        for f in files:
            p = os.path.join(dirpath, f)
            out[os.path.relpath(p, root).replace("\\", "/")] = p
    return out


def check_tree():
    if not os.path.isdir(SRC):
        return [("真源目录不存在：%s" % SRC, "bad")], ["缺 skills/course-selflearn"]
    if not os.path.isdir(PLUGIN_SKILL):
        return [("插件副本目录不存在：%s" % PLUGIN_SKILL, "bad")], ["缺 plugin/skills/course-selflearn"]
    a, b = tree(SRC), tree(PLUGIN_SKILL)
    rows, bad = [], []
    for rel in sorted(set(a) | set(b)):
        if rel not in a:
            rows.append((rel, "插件多出这个文件（真源里没有）", "bad"))
            bad.append(rel)
        elif rel not in b:
            rows.append((rel, "插件缺这个文件", "bad"))
            bad.append(rel)
        elif sha(a[rel]) != sha(b[rel]):
            rows.append((rel, "★字节不同（改一边没同步）", "bad"))
            bad.append(rel)
        else:
            rows.append((rel, "逐字一致 %s" % sha(a[rel])[:16], "ok"))
    return rows, bad


def check_bundle():
    pj = os.path.join(PLUGIN, "package.json")
    patch = os.path.join(PLUGIN, "cordis.patch.yml")
    if not os.path.isfile(pj):
        return [("缺 plugin/package.json", "bad")], ["缺 plugin/package.json"]
    if not os.path.isfile(patch):
        return [("缺 plugin/cordis.patch.yml", "bad")], ["缺 plugin/cordis.patch.yml"]
    rows, bad = [], []

    def ok(cond, msg):
        rows.append((msg, "ok" if cond else "bad"))
        if not cond:
            bad.append(msg)

    try:
        pkg = json.load(io.open(pj, encoding="utf-8"))
    except Exception as e:
        return [("package.json 解析失败：%s" % e, "bad")], ["package.json 解析失败"]
    name = pkg.get("name") or ""
    ok(pkg.get("type") == "module", "package.json type == module")
    ok("./package.json" in (pkg.get("exports") or {}),
       "exports 暴露 ./package.json（patch 里的 createRequire 要解析它）")
    files = pkg.get("files") or []
    for need in ("cordis.patch.yml", "skills"):
        ok(need in files, "files[] 含 %s" % need)
    ok(not os.path.isfile(os.path.join(PLUGIN, "index.js")),
       "没有 index.js（config-only：导入交给 loader）")

    bundle = ((pkg.get("dsh") or {}).get("bundle") or {})
    patch_rel = bundle.get("patch")
    ok(patch_rel == "./cordis.patch.yml", "dsh.bundle.patch == ./cordis.patch.yml")
    ok(bool(patch_rel) and os.path.isfile(os.path.join(PLUGIN, patch_rel)),
       "dsh.bundle.patch 指向的文件存在")

    text = io.open(patch, encoding="utf-8").read()
    ok(re.search(r"(?m)^\s*-\s*insert:\s*$", text) is not None,
       "patch 用 `- insert:` 包住（裸 `- id:` 会被当覆盖，warn+skip）")
    ok(("name: '%s'" % PROVIDER) in text or ('name: "%s"' % PROVIDER) in text,
       "patch 的 name 是官方 provider %s" % PROVIDER)
    ok("includeDefaultRoots: false" in text,
       "includeDefaultRoots: false（只读本包 skills/）")
    ok("customSkillDirs:" in text, "patch 里有 customSkillDirs")
    ok(bool(name) and (name + "/package.json") in text,
       "!!js 解析的是本包 package.json")
    ok("createRequire(baseUrl)" in text, "!!js 用 createRequire(baseUrl)（baseUrl = 所在 cordis.yml 的目录）")

    skill_md = os.path.join(PLUGIN_SKILL, "SKILL.md")
    if os.path.isfile(skill_md):
        head = io.open(skill_md, encoding="utf-8").read()[:2000]
        m = re.search(r"(?m)^name:\s*(\S+)\s*$", head)
        sk = m.group(1) if m else ""
        ok(sk == "course-selflearn", "SKILL.md 的 frontmatter name 与目录同名")
        ok(bool(SKILL_NAME_RE.match(sk or "")), "name 符合 kebab-case（注册表会拒非法名）")
    else:
        ok(False, "插件里缺 skills/course-selflearn/SKILL.md")
    return rows, bad


def main(argv):
    as_json = "--json" in argv[1:]
    if not os.path.isdir(PLUGIN):
        print("× 插件目录不存在：%s" % PLUGIN)
        return 2
    rows1, bad1 = check_tree()
    rows2, bad2 = check_bundle()
    bad = bad1 + bad2
    n_files = sum(1 for r in rows1 if r[1].startswith("逐字一致"))

    if as_json:
        print(json.dumps({
            "ok": not bad,
            "source": os.path.relpath(SRC, ROOT).replace("\\", "/"),
            "plugin": os.path.relpath(PLUGIN_SKILL, ROOT).replace("\\", "/"),
            "filesIdentical": n_files,
            "checks": len(rows2),
            "problems": bad,
        }, ensure_ascii=False))
        return 0 if not bad else 1

    print("== 真源 ↔ 插件副本 ==")
    for rel, msg, lvl in rows1:
        print("   %s %-34s %s" % ("  " if lvl == "ok" else "× ", rel, msg))
    print("\n== bundle 自洽 ==")
    for msg, lvl in rows2:
        print("   %s %s" % ("  " if lvl == "ok" else "× ", msg))
    print("\n逐字一致 %d 个文件 / bundle 检查 %d 项；问题 %d 个" % (n_files, len(rows2), len(bad)))
    if bad:
        print("修法：内容不同就从真源二进制重拷（shutil.copyfile），不要手抄；bundle 写错就改文件。")
        return 1
    print("结论：PASS —— 插件副本与真源逐字一致，且 bundle 自洽")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
