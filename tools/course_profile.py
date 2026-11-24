# -*- coding: utf-8 -*-
"""course_profile.py —— 给一门课做画像，并给出「这门课的讲义该怎么变」

为什么要有这一步
----------------
同一套讲义骨架套在所有课上，会同时犯两种错：
  - 数学课用「结论式」写法 → 学生背下结论却不会推第二步；
  - 电路课用「机制式」长篇推导 → 该背的判据没背下来，考试写不出。

所以每门课开工前先定画像，画像决定四件事：**骨架 / 图策略 / 字数预算 / 红线集**，
外加自测题型。画像是**可审计的字面量**，不是"你看着办"。

用法
----
  # 交互式问答（不传参数时逐项问）
  python tools/course_profile.py --out course-notes

  # 一次性给全（适合脚本调用）
  python tools/course_profile.py --out course-notes \
      --course "模拟电子技术" --discipline ee --presentation blackboard \
      --style derivation --assessment exam --language zh

  # 从已有的 _course.json 读（只补缺失项）
  python tools/course_profile.py --out course-notes --from-json course-notes/_course.json

产物：<out>/00-课程画像.md（人读）+ 同内容 JSON 段（脚本读）。

退出码：0 成功；2 参数不合法。
"""
import argparse
import io
import json
import os
import sys

# Windows 控制台默认 GBK，中文输出会乱码；统一转 UTF-8（失败就算了，不影响逻辑）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 每个轴的取值与含义（写死在表里，别在调用处散落判断）────────────────
AXES = {
    "discipline": {
        "math": "数学（高数/线代/概率）：以推导链和定义域为主",
        "ee": "电类（电路/模电/数电/信号）：以判据、工作点、波形为主",
        "physics": "物理：以模型假设与量纲为主",
        "cs": "计算机：以数据流、复杂度、可运行代码为主",
        "humanities": "人文社科：以论证结构与史料为主",
        "other": "其它（按通用规则，不猜）",
    },
    "presentation": {
        "ppt": "投影/PPT 为主：以屏幕上的字为准，口播补理由",
        "blackboard": "板书为主：以板书为准，口播容易串词",
        "mixed": "投影+板书混用：分段判定谁为准",
        "handout": "讲义/PDF 为主：以文档结构为准，视频只是补充",
    },
    "style": {
        "derivation": "推导密集：一步不能跳，跳了读者就断链",
        "judgement": "判据密集：重点是条件与阈值，不是推导",
        "concept": "概念记忆：重点是区分易混项与反例",
        "practice": "实操/代码：重点是能跑起来与排错顺序",
        "narrative": "叙事论证：重点是结构与证据",
    },
    "assessment": {
        "exam": "考试导向：要能算出数、写出判据",
        "project": "能做出东西：要能复现步骤、能排错",
        "understanding": "理解导向：要能解释给人听",
    },
    "language": {
        "zh": "中文授课：术语给中英对照",
        "en": "英文材料/授课：术语必须给中文解释",
        "mixed": "中英混讲：两边术语都要落表",
    },
}

# ── 学科 → 红线集（讲义里出现这些就算不合格）──────────────────────────
REDLINES = {
    "ee": [
        "把「理想运放」假设当普适结论（未写适用条件）",
        "波形图与文字描述的方向/极性不一致",
        "工作点与交流量混写，量符号不区分（U_BEQ vs u_be）",
        "单位缺失或量级错（mV 写成 V、kΩ 写成 Ω）",
        "把近似条件（如 β 很大、r_bb' 可忽略）写成恒等式",
        "公式里的符号在正文里没定义过",
    ],
    "math": [
        "定理的适用条件没写（开区间/连续/可导）",
        "极限与求导顺序混用未说明",
        "换元后积分限没跟着换",
        "把「充分条件」写成「充要条件」",
        "图形与解析式不一致（渐近线、奇点）",
        "等号链中间步骤跳步，读者无法复算",
    ],
    "physics": [
        "模型假设未声明（质点/刚体/理想气体）",
        "量纲不平衡",
        "近似使用未说明误差范围",
        "矢量方向与坐标系不一致",
        "实验条件与结论适用范围不符",
        "图像斜率/截距的物理意义写错",
    ],
    "cs": [
        "复杂度分析漏掉最坏情况",
        "代码块与文字描述的逻辑不一致",
        "边界条件（空、越界、溢出）未提",
        "把实现细节当算法性质",
        "伪代码与真实代码混淆",
        "依赖版本/环境未说明导致跑不起来",
    ],
    "humanities": [
        "引文无出处或出处与原文不符",
        "把作者观点当作事实陈述",
        "时间线与事件因果倒置",
        "概念在文中前后定义不一致",
        "以偏概全（个例推全体）",
        "译名前后不统一",
    ],
    "other": [
        "关键结论没有证据位置",
        "术语前后不一致",
        "图文不对应",
        "把推测写成事实",
    ],
}

# ── 呈现方式 → 图策略 ──────────────────────────────────────────────
FIGURE_POLICY = {
    "ppt": "以投影截图为主：优先截整页，不裁字；口播补充的理由写进正文而不是图里。"
           "老师跳过的页要标「跳过」。",
    "blackboard": "以板书截图为主：公式必须整行完整（左右不能切掉），"
                  "裁切放大到能看清上下标；口播与板书冲突时以板书为准并注明。",
    "mixed": "分段判定：先按 `course_scan.py` 的段落表标出每段是投影还是板书，"
             "同段内以画面为准；跨段引用时两处都给时刻。",
    "handout": "以讲义原文排版为主：截图只用于老师口头补充而文档里没有的内容。",
}

# ── 骨架：style × assessment → 每知识点的固定小结构 ─────────────────
SKELETON = {
    ("derivation", "exam"): [
        "起点（已知条件与适用前提）", "推导（一步一行，不跳步）",
        "结论（可直接抄进答题纸的形式）", "常见考法（会怎么被问）",
    ],
    ("derivation", "understanding"): [
        "要解决的问题", "为什么必须这样推（每步的动机）", "结论与它的几何/物理含义", "换条件会怎样",
    ],
    ("judgement", "exam"): [
        "判据（条件与阈值）", "判据从哪来（一句话，不展开）",
        "典型取值表", "反例（什么时候不成立）",
    ],
    ("judgement", "project"): [
        "判据", "怎么测（仪器/接法）", "读数怎么读到这个判据", "读错时先查哪里",
    ],
    ("concept", "exam"): [
        "定义（一句话）", "与最易混的那一项的区别（表格）", "反例", "记忆钩子",
    ],
    ("concept", "understanding"): [
        "它解决什么问题", "定义", "与相邻概念的关系", "一个能讲给别人听的例子",
    ],
    ("practice", "project"): [
        "目标（做完能观察到什么）", "步骤（可复现）", "判据（看到什么算成功）",
        "失败排查顺序（按概率排）",
    ],
    ("narrative", "understanding"): [
        "论点", "证据链", "反方与回应", "它的适用边界",
    ],
}
GENERIC_SKELETON = [
    "这一条解决什么问题", "核心内容（定义/推导/判据）",
    "证据位置（时间点或行号）", "怎么自测",
]

# ── 字数预算系数：以「每知识点 400 字」为 1.0 ────────────────────────
BUDGET_FACTOR = {
    "math": 1.25, "ee": 1.0, "physics": 1.0, "cs": 1.1,
    "humanities": 0.85, "other": 1.0,
}
STYLE_FACTOR = {
    "derivation": 1.2, "judgement": 0.95, "concept": 0.85,
    "practice": 1.15, "narrative": 0.9,
}


def pick_quiz(discipline, style, assessment):
    """自测题型：学科决定形式，风格决定难度落点。"""
    base = {
        "math": ["计算题（给数算到底）", "判条件（该定理能不能用）", "改错（跳步/条件错）"],
        "ee": ["算工作点", "判断波形与极性", "给电路说现象（为什么这个电阻一改就烧）"],
        "physics": ["量纲自查", "模型选择（能不能当质点）", "估算题（给数量级）"],
        "cs": ["复杂度分析", "边界用例设计", "读代码说输出"],
        "humanities": ["复述论证结构", "找反例", "史料与结论是否匹配"],
        "other": ["复述要点", "指证据位置", "举一个反例"],
    }.get(discipline, ["复述要点", "指证据位置", "举一个反例"])
    if assessment == "exam":
        base = base + ["限时闭卷模拟（只给题不给提示）"]
    elif assessment == "project":
        base = base + ["从零复现一遍并记录卡点"]
    else:
        base = base + ["讲给一个没学过的人听（3 分钟）"]
    if style == "derivation":
        base.append("抽一步问「为什么可以这样」")
    elif style == "judgement":
        base.append("给一个刚好在阈值上的例子判成不成立")
    return base


def build(course, discipline, presentation, style, assessment, language):
    skel = SKELETON.get((style, assessment)) or SKELETON.get((style, "exam")) or GENERIC_SKELETON
    bf = BUDGET_FACTOR.get(discipline, 1.0) * STYLE_FACTOR.get(style, 1.0)
    per_point = int(round(400 * bf))
    return {
        "course": course,
        "discipline": discipline,
        "presentation": presentation,
        "style": style,
        "assessment": assessment,
        "language": language,
        "skeleton": skel,
        "figure_policy": FIGURE_POLICY[presentation],
        "word_budget_per_point": per_point,
        "word_budget_note": "预算 = 知识点数 × %d，合格带 = 预算 +800（%s × %s）"
                            % (per_point, BUDGET_FACTOR.get(discipline, 1.0),
                               STYLE_FACTOR.get(style, 1.0)),
        "redlines": REDLINES.get(discipline, REDLINES["other"]),
        "quiz": pick_quiz(discipline, style, assessment),
        "precedence": [
            "① 读者没有纠错能力（第一原则）",
            "② 本画像（学科/呈现/风格）",
            "③ 通用讲义规范",
        ],
    }


def render(p):
    L = ["# %s · 课程画像" % p["course"], "",
         "> 本文件由 `tools/course_profile.py` 生成；改画像请重跑脚本，别手改。",
         "> 它决定这份讲义**长什么样**，写之前先读一遍。", "",
         "## 这门课的判定", "",
         "| 轴 | 取值 | 含义 |", "| --- | --- | --- |"]
    for k in ("discipline", "presentation", "style", "assessment", "language"):
        L.append("| %s | `%s` | %s |" % (k, p[k], AXES[k][p[k]]))
    L += ["", "## 每知识点固定骨架（照这个顺序写，别自创）", ""]
    for i, s in enumerate(p["skeleton"], 1):
        L.append("%d. %s" % (i, s))
    L += ["", "## 图策略", "", p["figure_policy"], "",
          "## 字数预算", "",
          "- 每个知识点 **%d 字**" % p["word_budget_per_point"],
          "- %s" % p["word_budget_note"], "",
          "## 红线（出现即不合格，复查时逐条过）", ""]
    for r in p["redlines"]:
        L.append("- [ ] %s" % r)
    L += ["", "## 自测题型", ""]
    for q in p["quiz"]:
        L.append("- %s" % q)
    L += ["", "## 冲突时谁优先", ""]
    for i, s in enumerate(p["precedence"]):
        L.append("%d. %s" % (i + 1, s) if i else s)
    L += ["", "## 怎么被 agent 用", "",
          "写讲义前读本文件；每写完一个知识点，对照「骨架」看四项是否齐、"
          "对照「红线」扫一遍。`note_check.py` 只能查机械项，"
          "**学科红线必须靠这份清单人工过**。", ""]
    return "\n".join(L)


def ask(prompt, choices, default):
    print("\n%s" % prompt)
    for k, v in choices.items():
        print("  %-12s %s" % (k, v))
    got = input("选择 [%s]: " % default).strip() or default
    if got not in choices:
        print("  不认识的取值 %r，用默认 %s" % (got, default))
        return default
    return got


def main():
    ap = argparse.ArgumentParser(description="给一门课做画像，输出讲义适配卡")
    ap.add_argument("--out", default="course-notes", help="讲义库根目录（默认 ./course-notes）")
    ap.add_argument("--course", default="", help="课程名，如 模拟电子技术")
    ap.add_argument("--from-json", default="", help="从已有 _course.json 读缺失项")
    for k, v in AXES.items():
        ap.add_argument("--" + k, choices=list(v.keys()), default=None)
    ap.add_argument("--quiet", action="store_true", help="不交互，缺项用默认值")
    a = ap.parse_args()

    pre = {}
    if a.from_json and os.path.isfile(a.from_json):
        try:
            pre = json.load(open(a.from_json, encoding="utf-8"))
        except Exception as e:
            print("读 %s 失败：%s（忽略）" % (a.from_json, e))

    course = a.course or pre.get("course") or "未命名课程"
    vals = {}
    defaults = {"discipline": "other", "presentation": "mixed",
                "style": "concept", "assessment": "understanding", "language": "zh"}
    for k, choices in AXES.items():
        v = getattr(a, k) or pre.get(k)
        if not v:
            v = defaults[k] if a.quiet or not sys.stdin.isatty() else ask(
                "这门课的 %s：" % k, choices, defaults[k])
        vals[k] = v

    prof = build(course, **vals)
    os.makedirs(a.out, exist_ok=True)
    dst = os.path.join(a.out, "00-课程画像.md")
    io.open(dst, "w", encoding="utf-8", newline="\n").write(render(prof) + "\n")

    print("\n写出：%s" % dst)
    print("骨架：%s" % " → ".join(prof["skeleton"]))
    print("字数预算：每知识点 %d 字" % prof["word_budget_per_point"])
    print("红线 %d 条 / 自测 %d 类" % (len(prof["redlines"]), len(prof["quiz"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
