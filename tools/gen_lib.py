# -*- coding: utf-8 -*-
"""扫描讲义库 frontmatter，自动生成 00-索引.md / 00-课程地图.md / 99-台账.md

用法：
  py gen_lib.py [库目录]        # 不传则用 $COURSE_LIB 或 <脚本上级目录>/course-notes

时长优先从 <库目录>/_course.json 的 episodes[].duration 读，读不到显示「—」。
不要在这里写死时长表：换一门课就全错。
"""
import os, re, json, datetime, sys

BASE = (sys.argv[1] if len(sys.argv) > 1
        else os.environ.get("COURSE_LIB")
        or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "course-notes"))

# 课程显示名：优先读 _course.json 的 course 字段
try:
    _cj = json.load(open(os.path.join(BASE, "_course.json"), encoding="utf-8"))
except Exception:
    _cj = {}
COURSE_NAME = _cj.get("course") or os.path.basename(os.path.abspath(BASE)) or "课程"


def _dur_from_json():
    p = os.path.join(BASE, "_course.json")
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    return {"p%02d" % e["n"]: e.get("duration") for e in d.get("episodes", [])}


def fm(path):
    if not os.path.exists(path):
        return {}
    t = open(path, encoding="utf-8").read()
    if not t.startswith("---"):
        return {}
    end = t.index("\n---", 3)
    d = {}
    for line in t[3:end].split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d


if not os.path.isdir(BASE):
    print(f"库目录不存在：{BASE}")
    print("用法：py gen_lib.py <库目录>   或设 COURSE_LIB")
    sys.exit(2)

eps = sorted([d for d in os.listdir(BASE) if re.match(r"^ep\d\d-", d)])
rows = []
for d in eps:
    n = int(d[2:4])
    topic = d[5:]
    kp = fm(os.path.join(BASE, d, "00-知识点.md"))
    lec = fm(os.path.join(BASE, d, "10-讲义.md"))
    has_lec = bool(lec)
    assets = os.path.join(BASE, d, "assets")
    imgs = sorted([f for f in os.listdir(assets)]) if os.path.isdir(assets) else []
    kps = re.findall(r"K\d+_[^,\]\s]+", lec.get("knowledge_points", "") or kp.get("knowledge_points", ""))
    rows.append(dict(n=n, dir=d, topic=topic, kp=kp, lec=lec, has_lec=has_lec,
                     imgs=imgs, kps=kps,
                     review=os.path.join(BASE, d, "90-复查", "ep%02d-review.md" % n)))

today = datetime.date.today().isoformat()

# ---- 00-课程地图.md ----
L = ["# %s · 课程地图" % COURSE_NAME, "",
     "> 本文件自动生成（`tools/gen_lib.py`），手改会被下次生成覆盖。",
     "> 状态列由**文件存在性**推出，不手填。", "",
     "| 集 | 标题 | 时长 | 讲义 | status | figures | 知识点 | 图 |",
     "| --- | --- | --- | --- | --- | --- | --- | --- |"]
for r in rows:
    dur = _dur_from_json().get("p%02d" % r["n"])
    durs = ("%d:%02d" % (int(dur)//60, int(dur) % 60)) if dur else "—"
    L.append("| ep%02d | %s | %s | %s | %s | %s | %d | %d |" % (
        r["n"], r["topic"], durs,
        "✅" if r["has_lec"] else "❌",
        r["lec"].get("status", "—"),
        r["lec"].get("figures", "—"),
        len(r["kps"]), len(r["imgs"])))
L += ["", "## 怎么读这张表", "",
      "- **讲义 ❌**：这一集还没成稿（可能是素材已到、还没写，或根本没抓）。",
      "- **status = draft**：稿子写完，等复查；**reviewed**：复查通过。",
      "- **图 0**：正文里没有引用任何原片截图，按规范这种图要么补讲要么删。", ""]
open(os.path.join(BASE, "00-课程地图.md"), "w", encoding="utf-8").write("\n".join(L))

# ---- 00-索引.md ----
L = ["# %s · 索引" % COURSE_NAME, "",
     "> 自动生成，勿手改。按「集 → 知识点」列，带 status 与 evidence。", ""]
for r in rows:
    L.append("## ep%02d · %s" % (r["n"], r["topic"]))
    L.append("")
    L.append("- 目录：`%s/`" % r["dir"])
    L.append("- status：%s" % r["lec"].get("status", "—"))
    L.append("- evidence：%s" % (r["lec"].get("evidence") or r["kp"].get("evidence") or "—"))
    L.append("- 复查表：`90-复查/ep%02d-review.md`" % r["n"])
    if r["kps"]:
        L.append("- 知识点（%d 个）：" % len(r["kps"]))
        for k in r["kps"]:
            L.append("  - `%s`" % k)
    L += ["", ""]
open(os.path.join(BASE, "00-索引.md"), "w", encoding="utf-8").write("\n".join(L))

# ---- 99-台账.md ----
total = len(rows)
done = sum(1 for r in rows if r["has_lec"])
draft = sum(1 for r in rows if r["lec"].get("status") == "draft")
reviewed = sum(1 for r in rows if r["lec"].get("status") == "reviewed")
noimg = sum(1 for r in rows if r["has_lec"] and not r["imgs"])
L = ["# %s · 台账" % COURSE_NAME, "", "> 自动生成，勿手改。生成日：%s" % today, "",
     "| 项 | 数 |", "| --- | --- |",
     "| 有目录的集 | %d |" % total,
     "| 已成讲义 | %d |" % done,
     "| draft（待复查/待改） | %d |" % draft,
     "| reviewed（复查通过） | %d |" % reviewed,
     "| 缺图 | %d |" % noimg, ""]
L += ["## 逐集状态", "", "| 集 | status | 知识点 | 图 | 缺口 |", "| --- | --- | --- | --- | --- |"]
for r in rows:
    gaps = []
    if not r["has_lec"]:
        gaps.append("缺讲义")
    if r["has_lec"] and not r["imgs"]:
        gaps.append("缺图")
    if not os.path.exists(r["review"]):
        gaps.append("缺复查表")
    L.append("| ep%02d | %s | %d | %d | %s |" % (
        r["n"], r["lec"].get("status", "—"), len(r["kps"]), len(r["imgs"]),
        "、".join(gaps) if gaps else "无"))
L += ["", "## 复查未通过清单", ""]
bad = [r for r in rows if r["lec"].get("status") == "draft"]
if bad:
    for r in bad:
        L.append("- ep%02d %s：`90-复查/ep%02d-review.md`" % (r["n"], r["topic"], r["n"]))
else:
    L.append("- 无")
L.append("")
open(os.path.join(BASE, "99-台账.md"), "w", encoding="utf-8").write("\n".join(L))
print("generated:", total, "集,", done, "有讲义")
