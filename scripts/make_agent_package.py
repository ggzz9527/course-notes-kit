#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""make_agent_package.py —— 把 skills/course-selflearn 打成别的 agent 能直接装的包

产物都在 `dist/`（.gitignore 忽略，不进仓库）：

    dist/skill-panel/course-selflearn/      目录：拷进该产品的技能目录即可
    dist/skill-panel/course-selflearn.zip   上传用：技能面板 → 添加技能 → 上传技能包
    dist/claude/course-selflearn/           ~/.claude/skills/ 或 <项目>/.claude/skills/
    dist/generic/course-selflearn/          任意 agent：把 SKILL.md 当系统提示的一部分

三个目标的**正文逐字相同**（都来自 skills/course-selflearn，二进制拷贝），只有 frontmatter
与附带的 `安装.txt` 不同：

  skill-panel/ 去掉 DSH 专有的 `whenToUse`（这类产品只读 name/description 一类公开字段），
             补 `version`/`tags`，让它在技能面板里有分类；
  claude/    原样（Claude Code 的 Agent Skills 同样读 name+description）；
  generic/   原样。

为什么用脚本而不是手工拷：三份副本最容易漂移的地方就是"只改了一份"；
这个脚本每次从真源重建，配合 `scripts/check_skill_sync.py` 守住 DSH 那份。

用法：
  python scripts/make_agent_package.py skill-panel
  python scripts/make_agent_package.py claude
  python scripts/make_agent_package.py generic
退出码：0 成功；2 源目录找不到。
"""
import io
import os
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "skills", "course-selflearn")
DIST = os.path.join(ROOT, "dist")

STEPS = {
    "skill-panel": [
        "技能面板 → 添加技能 → 上传技能包，选 course-selflearn.zip；或",
        "把 course-selflearn/ 整个目录拷到该产品的技能目录（多数是 ~/.<产品名>/skills/）后**完全重启**它。",
        "装完在会话里说一句「用 course-selflearn 整理这门课」，它就会加载本技能。",
        "注意：技能只带「怎么做」；跑管线仍需要本仓库的 tools/ 与 Python 环境——",
        "      先把仓库 clone 到那台机器，再在会话里把工作目录指到仓库根。",
    ],
    "claude": [
        "把 course-selflearn/ 拷到 ~/.claude/skills/（对所有项目生效）",
        "或 <项目>/.claude/skills/（只对该项目生效）。",
        "Claude Code 用 Agent Skills 的目录约定自动发现，无需注册。",
        "跑管线时需要本仓库的 tools/ 与 Python 环境。",
    ],
    "generic": [
        "把 course-selflearn/SKILL.md 作为系统提示的一部分喂给 agent；",
        "或把整个目录放进它的「技能/知识」目录（若它支持目录式技能）。",
        "references/*.md 是正文里引用的补充规范，让 agent 能按路径读取即可。",
        "跑管线需要本仓库的 tools/ 与 Python 环境（见 README「安装」）。",
    ],
}


def split_frontmatter(text):
    """返回 (frontmatter 行列表, 正文)。第一行不是 --- 就当没有 frontmatter。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return [], text


def rewrite_frontmatter(meta, target):
    """按目标重写 frontmatter；skill-panel 只留公开字段并补 version/tags。"""
    kv = {}
    for line in meta:
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    if target == "skill-panel":
        out = ["name: %s" % kv.get("name", "course-selflearn"),
               "description: %s" % kv.get("description", ""),
               "version: 1.0.0",
               "tags: [course, lecture-notes, teaching]"]
    else:
        out = list(meta)
    return "\n".join(out)


def build(target):
    if not os.path.isdir(SRC):
        print("× 源目录不存在：%s" % SRC)
        return 2
    out_root = os.path.join(DIST, target)
    pkg = os.path.join(out_root, "course-selflearn")
    if os.path.isdir(out_root):
        shutil.rmtree(out_root)
    os.makedirs(pkg)

    for dirpath, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
        rel = os.path.relpath(dirpath, SRC)
        dst_dir = pkg if rel == "." else os.path.join(pkg, rel)
        os.makedirs(dst_dir, exist_ok=True)
        for f in files:
            src = os.path.join(dirpath, f)
            dst = os.path.join(dst_dir, f)
            if f == "SKILL.md" and target in ("skill-panel",):
                meta, body = split_frontmatter(io.open(src, encoding="utf-8").read())
                text = "---\n%s\n---\n%s" % (rewrite_frontmatter(meta, target), body)
                io.open(dst, "w", encoding="utf-8", newline="\n").write(text)
            else:
                shutil.copyfile(src, dst)      # 二进制拷：文本模式会把 LF 翻成 CRLF

    io.open(os.path.join(pkg, "安装.txt"), "w", encoding="utf-8", newline="\n").write(
        "course-selflearn —— 装到 %s\n\n" % target +
        "\n".join("- " + s for s in STEPS[target]) + "\n")

    # 压缩包先写到 dist/ 根（不能写进 out_root：make_archive 边写边打包，
    # 会把自己也收进去——实测出现过 course-selflearn.zip 里套一个 course-selflearn.zip）。
    tmp_base = os.path.join(DIST, "_%s_course-selflearn" % target)
    shutil.make_archive(tmp_base, "zip", root_dir=out_root, base_dir="course-selflearn")
    zip_path = os.path.join(out_root, "course-selflearn.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    os.replace(tmp_base + ".zip", zip_path)
    n = sum(len(fs) for _, _, fs in os.walk(pkg))
    print("打包完成：%s" % zip_path)
    print("  目录 %s（%d 个文件）" % (pkg, n))
    print("  装法：")
    for s in STEPS[target]:
        print("    - " + s)
    return 0


def main(argv):
    targets = [a for a in argv[1:] if not a.startswith("-")]
    if not targets:
        print("用法：python scripts/make_agent_package.py {skill-panel|claude|generic}")
        return 2
    for t in targets:
        if t not in STEPS:
            print("× 未知目标：%s（可选：%s）" % (t, ", ".join(STEPS)))
            return 2
    rc = 0
    for t in targets:
        rc |= build(t)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
