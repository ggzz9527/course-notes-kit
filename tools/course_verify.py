#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
course_verify.py —— 讲义机械项核验工具（验收/爬课/讲稿共用）

三个子命令，接口定死：
  wc      <集目录>                      字数 / 单点 / 图解读 / status / 禁用词 / 图片引用概要
  refs    <集目录>                      图片引用逐条解析 + 孤图 + 旧文件名残留
  frames  <video> <frames_dir> [--interval N] [--out frames.csv]
                                        抽帧真实时刻测量（精确 seek + 逐像素比对）

铁律：
  - 只读讲义库，绝不写；
  - 只吐数字和清单，不出「通过/不通过」结论（结论由验收在复查表里下）；
  - frames 唯一产出是 frames.csv（帧名/标称秒/实测真实秒/残差）。

字数口径（权威，逐字对齐 ep02/03/04/05 复查锚点）：
  正文 = 所有行按 CJK 计数，排除——
    frontmatter、```代码块```、$$公式块$$（含单行 $$...$$）、|表格行|、
    ![图片行]、<details>折叠答案</details>、#节标题、`> 图 N ·`图注引导句、`逐点解读：`行。
  预算 = knowledge_points 数 × 400，硬上限 6000。
  单点 ≤ 400（只对 K<N> 段），图后逐点解读 ≤ 120（每个 K 段的逐点解读行）。

运行：tools\\py.cmd course_verify.py <subcommand> ...
"""

import argparse
import io
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------- 基础工具

def cjk_count(s: str) -> int:
    return len(re.findall(r'[\u4e00-\u9fff]', s))


def strip_frontmatter(text: str) -> str:
    return re.sub(r'^---\n.*?\n---\n', '', text, flags=re.S)


_FIGCAP = re.compile(r'^>\s*图\s*\d+\s*[··.]')
_DUODIAN = re.compile(r'^逐点解读[:：]')

# 禁用词（讲解工具箱口径）
BANNED = ["显然", "很简单", "众所周知", "大家应该都知道"]


def _is_code_fence(t: str) -> bool:
    return t.startswith('```')


def _is_formula_fence(t: str) -> bool:
    return t.startswith('$$')


def _count_body_cjk(lines):
    """返回 (正文cjk, 逐点解读cjk, 分类dict)。逐点解读单独计，不计入正文。"""
    n = len(lines)
    in_code = in_formula = in_details = False
    body = 0
    duodian = 0
    cat = {"code": 0, "formula": 0, "table": 0, "img": 0,
           "details": 0, "heading": 0, "figcap": 0, "duodian": 0}
    i = 0
    while i < n:
        s = lines[i]
        t = s.strip()
        if in_code:
            cat["code"] += cjk_count(s)
            if t.startswith('```'):
                in_code = False
            i += 1
            continue
        if in_formula:
            cat["formula"] += cjk_count(s)
            if t.startswith('$$'):
                in_formula = False
            i += 1
            continue
        if in_details:
            cat["details"] += cjk_count(s)
            if '</details>' in t:
                in_details = False
            i += 1
            continue
        if t.startswith('```'):
            in_code = True
            cat["code"] += cjk_count(s)
            i += 1
            continue
        if t.startswith('$$'):
            if t.endswith('$$') and len(t) > 4:
                cat["formula"] += cjk_count(s)   # 单行 $$...$$
                i += 1
                continue
            in_formula = True
            cat["formula"] += cjk_count(s)
            i += 1
            continue
        if t.startswith('<details') or '<details>' in t:
            cat["details"] += cjk_count(s)
            if '</details>' not in t:
                in_details = True
            i += 1
            continue
        if t.startswith('|'):
            cat["table"] += cjk_count(s)
            i += 1
            continue
        if t.startswith('!['):
            cat["img"] += cjk_count(s)
            i += 1
            continue
        if t.startswith('#'):
            cat["heading"] += cjk_count(s)
            i += 1
            continue
        if t.startswith('>'):
            if _FIGCAP.match(t):
                cat["figcap"] += cjk_count(s)
            else:
                body += cjk_count(s)
            i += 1
            continue
        if _DUODIAN.match(t):
            cat["duodian"] += cjk_count(s)
            duodian += cjk_count(s)
            i += 1
            continue
        body += cjk_count(s)
        i += 1
        continue
    return body, duodian, cat


# ---------------------------------------------------------------- frontmatter

def read_frontmatter(path: str) -> dict:
    txt = io.open(path, encoding='utf-8').read()
    m = re.match(r'^---\n(.*?)\n---\n', txt, flags=re.S)
    fm = {}
    if not m:
        return fm
    for line in m.group(1).split('\n'):
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip()
    return fm


def count_knowledge_points(fm: dict) -> int:
    # knowledge_points: [K1_..., K2_..., ...]  —— 数 K<N>_ 项
    raw = fm.get('knowledge_points', '')
    return len(re.findall(r'K\d+_', raw))


# ---------------------------------------------------------------- 图片引用

_IMG_REF = re.compile(r'!\[[^\]]*\]\(([^)\s]+)\)')


def find_image_refs(md_text: str):
    """返回 [(引用路径, 在讲义中的原始串)]，含 .md 内所有 ![..](..)"""
    return _IMG_REF.findall(md_text)


def _resolve_ref(ref_path: str, ep_dir: str) -> bool:
    # ref_path 形如 assets/xxx.png（相对集目录）
    p = os.path.normpath(os.path.join(ep_dir, ref_path))
    return os.path.isfile(p)


def scan_images(ep_dir: str):
    """扫描集目录：讲义/知识点表里的引用 + assets/ 实际文件。
    返回 (refs[(path,src)], assets_files[], broken[], orphan[])"""
    jiangyi = os.path.join(ep_dir, '10-讲义.md')
    zhishidian = os.path.join(ep_dir, '00-知识点.md')
    refs = []
    for src, path in [('讲义', jiangyi), ('知识点表', zhishidian)]:
        if os.path.isfile(path):
            txt = io.open(path, encoding='utf-8').read()
            for rp in find_image_refs(txt):
                refs.append((rp, src))
    assets_dir = os.path.join(ep_dir, 'assets')
    assets = []
    if os.path.isdir(assets_dir):
        for root, _dirs, files in os.walk(assets_dir):
            for f in files:
                assets.append(os.path.relpath(os.path.join(root, f), ep_dir))
    # 归一化比较
    broken = []   # 引用了但盘上不存在
    seen = set()
    for rp, src in refs:
        if not _resolve_ref(rp, ep_dir):
            broken.append((rp, src))
        seen.add(os.path.normpath(rp).replace('\\', '/'))
    # 孤图：assets 里存在但没被任何引用
    ref_basenames = {os.path.basename(rp) for rp, _s in refs}
    orphan = []
    for a in assets:
        base = os.path.basename(a)
        if base not in ref_basenames and '/ref/' not in a.replace('\\', '/'):
            orphan.append(a)
    return refs, assets, broken, orphan


# ---------------------------------------------------------------- 子命令实现

def cmd_wc(ep_dir: str):
    jiangyi = os.path.join(ep_dir, '10-讲义.md')
    if not os.path.isfile(jiangyi):
        print(f"[wc] 找不到 {jiangyi}")
        return 1
    txt = io.open(jiangyi, encoding='utf-8').read()
    fm = read_frontmatter(jiangyi)
    nkp = count_knowledge_points(fm)
    budget = nkp * 400
    hard = 6000

    body = strip_frontmatter(txt)
    lines = body.split('\n')
    total, total_duodian, _cat = _count_body_cjk(lines)

    # 逐点切分：兼容两种标题约定——
    #   ep03/04/05：### K<N> · 名称
    #   ep02：## N. K<N> · 名称，且 ### N.M 为子点（不带 K）
    # 规则：标题里带 K<数字> 且数字变化 → 新段；## 无 K → 段边界；### 无 K → 子点，留在当前段。
    sections = []  # (K编号, 名称, [lines])
    cur_k = None
    cur_name = ''
    curlines = []
    def flush():
        nonlocal cur_k, curlines
        if cur_k is not None:
            sections.append((cur_k, cur_name, curlines))
        cur_k = None
        curlines = []
    for s in lines:
        t = s.strip()
        if t.startswith('###') or t.startswith('##'):
            m = re.search(r'K(\d+)', t)
            if m:
                newk = int(m.group(1))
                if newk != cur_k:
                    flush()
                    cur_k = newk
                    cur_name = t
                    curlines = []
                else:
                    # 同一 K 的重复标题（子点如 ### 4.1 不带 K 已落入 else 分支）
                    cur_name = t
            else:
                # 无 K 标题
                if t.startswith('##'):
                    flush()
                # ### 无 K = 子点，留在当前段
        else:
            if cur_k is not None:
                curlines.append(s)
    flush()

    # 单点正文 / 图后逐点解读
    per_point = []
    for k, name, ls in sections:
        b, d, _ = _count_body_cjk(ls)
        per_point.append((k, name, b, d))

    k_sections = per_point  # 现在 sections 已只含 K 段
    max_body = max((b for _k, _n, b, _d in k_sections), default=0)
    max_body_name = next((n for _k, n, b, _d in k_sections if b == max_body), '-')
    over400 = [(n, b) for _k, n, b, _d in k_sections if b > 400]
    max_duodian = max((d for _k, _n, _b, d in k_sections), default=0)
    over120 = [(n, d) for _k, n, _b, d in k_sections if d > 120]

    # status
    status = fm.get('status', '(无)')

    # 禁用词
    banned_hits = {w: txt.count(w) for w in BANNED if txt.count(w) > 0}

    # 图片引用概要
    refs, assets, broken, orphan = scan_images(ep_dir)

    print(f"== wc: {os.path.basename(ep_dir)} ==")
    print(f"字数: {total} / {budget} 字，{nkp} 个知识点（硬上限 {hard}）")
    print(f"status: {status}")
    print(f"单点正文: 最大 {max_body}（{max_body_name}），超400: {over400 or '无'}")
    print(f"图后逐点解读: 最大 {max_duodian}，超120: {over120 or '无'}")
    print(f"图片引用: {len(refs)} 处，assets {len(assets)} 文件，"
          f"坏引用 {len(broken)}，孤图 {len(orphan)}")
    if banned_hits:
        print(f"禁用词命中: {banned_hits}")
    else:
        print("禁用词命中: 无")
    # 明细（供复查表取证）
    print("-- 逐点正文 --")
    for k, n, b, d in k_sections:
        flag = " <-- 超400" if b > 400 else ""
        print(f"  K{k}  {n}  正文{b} 图解读{d}{flag}")
    if broken:
        print("-- 坏引用 --")
        for rp, src in broken:
            print(f"  [{src}] {rp}")
    if orphan:
        print("-- 孤图 --")
        for a in orphan:
            print(f"  {a}")
    return 0


def cmd_refs(ep_dir: str):
    refs, assets, broken, orphan = scan_images(ep_dir)
    print(f"== refs: {os.path.basename(ep_dir)} ==")
    print(f"引用 {len(refs)} 处 / assets {len(assets)} 文件 / "
          f"坏引用 {len(broken)} / 孤图 {len(orphan)}")
    for rp, src in refs:
        ok = _resolve_ref(rp, ep_dir)
        print(f"  [{'OK' if ok else 'MISS'}] [{src}] {rp}")
    if orphan:
        print("-- 孤图（assets 存在但无引用）--")
        for a in orphan:
            print(f"  {a}")
    return 0


def _frame_index(fname: str):
    """从帧文件名解析序号：f_0015 / f0015 → 15"""
    m = re.search(r'f_?(\d+)', os.path.basename(fname))
    return int(m.group(1)) if m else None


def _seconds_from_name(fname: str):
    """从「文件名即真实秒」解析秒数：f_145.png → 145.0；f_145_5.png → 145.5。
    只认纯数字（含可选 .5）紧跟 f/f_ 之后的秒值。"""
    base = os.path.basename(fname)
    m = re.search(r'f_?(\d+)(?:_(\d))?', base)
    if not m:
        return None
    sec = float(m.group(1))
    if m.group(2):
        sec += float(m.group(2)) / 10.0
    return sec


def _extract_frame(video: str, t_sec: float, out_png: str):
    """精确 seek（-ss 必须在 -i **之前**，走 input seek；放后面会从头解码整段视频，
    48 分钟 720P 一帧要一两分钟——2026-11-21 实测卡了半小时才发现）抽一帧到 out_png。
    注意：沙箱禁命名管道，故 stdout/stderr 一律 DEVNULL，不 capture。"""
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error',
           '-ss', f'{t_sec:.3f}', '-i', video,
           '-frames:v', '1', '-y', out_png]
    subprocess.run(cmd, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _mad(img_a, img_b):
    """平均绝对差（0-255 尺度），两图先 resize 到同一尺寸。"""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        print("[frames] 需要 PIL / numpy（tools/venv 已带）", file=sys.stderr)
        sys.exit(1)
    a = Image.open(img_a).convert('RGB')
    b = Image.open(img_b).convert('RGB')
    w, h = a.size
    if b.size != (w, h):
        b = b.resize((w, h))
    A = np.asarray(a, dtype=np.float64)
    B = np.asarray(b, dtype=np.float64)
    return float(np.mean(np.abs(A - B)))


def _seek_and_mad(video, fpath, t_sec, tmp, tag):
    """精确 seek 抽 t_sec 参考帧，与 fpath 逐像素比，返回 (残差 or None)。"""
    ref = os.path.join(tmp, f'_ref_{tag}_{int(t_sec*10)}.png')
    _extract_frame(video, t_sec, ref)
    if not os.path.isfile(ref):
        return None
    return _mad(fpath, ref)


def cmd_frames(video: str, frames_dir: str, interval, out_csv: str, by_name: bool):
    if not os.path.isfile(video):
        print(f"[frames] 找不到视频 {video}")
        return 1
    if not os.path.isdir(frames_dir):
        print(f"[frames] 找不到帧目录 {frames_dir}")
        return 1
    files = sorted(f for f in os.listdir(frames_dir)
                   if f.lower().endswith(('.png', '.jpg', '.jpeg')))
    if not files:
        print("[frames] 帧目录里没有图片")
        return 1

    # 沙箱系统临时目录清理会报 WinError 5，改用工作区内的临时目录
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '_frames_tmp_%d' % os.getpid())
    os.makedirs(tmp, exist_ok=True)
    rows = []          # (帧名, 标称秒/声称秒, 实测真实秒, 残差, 状态)
    try:
        for f in files:
            fpath = os.path.join(frames_dir, f)
            if by_name:
                claimed = _seconds_from_name(f)
                if claimed is None:
                    rows.append((f, None, None, None, 'skip(无法解析秒)'))
                    continue
                d_claimed = _seek_and_mad(video, fpath, claimed, tmp,
                                          f'{claimed}_claimed')
                if d_claimed is None:
                    rows.append((f, claimed, None, None, 'skip(seek失败)'))
                    continue
                if d_claimed < 2.0:
                    # 声称秒画面吻合 → 正常，实测真实秒 = 声称秒
                    rows.append((f, claimed, claimed, round(d_claimed, 2),
                                 'OK'))
                else:
                    # 声称秒不吻合 → 异常；在 ±1s 里找最像的当修正提示
                    best_t = claimed
                    best_d = d_claimed
                    for t in (claimed - 1.0, claimed + 1.0):
                        d = _seek_and_mad(video, fpath, t, tmp, f'{claimed}_{t}')
                        if d is not None and d < best_d:
                            best_d = d
                            best_t = t
                    rows.append((f, claimed, best_t, round(best_d, 2),
                                 '异常'))
            else:
                idx = _frame_index(f)
                if idx is None:
                    rows.append((f, None, None, None, 'skip(无法解析序号)'))
                    continue
                nominal = (idx - 1) * interval
                half = interval / 2.0
                cands = [nominal, nominal + half, nominal + interval]
                best_t = None
                best_d = float('inf')
                for t in cands:
                    d = _seek_and_mad(video, fpath, t, tmp, f'{idx}_{t}')
                    if d is None:
                        continue
                    if d < best_d:
                        best_d = d
                        best_t = t
                rows.append((f, nominal, best_t, round(best_d, 2), ''))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    if by_name:
        header = '帧名,文件名秒,实测真实秒,残差,状态'
    else:
        header = '帧名,标称秒,实测真实秒,残差'
    if out_csv:
        with io.open(out_csv, 'w', encoding='utf-8', newline='') as fo:
            fo.write(header + '\n')
            for f, nom, bt, bd, st in rows:
                fo.write(f'{f},{nom if nom is not None else ""},'
                         f'{bt if bt is not None else ""},{bd},{st}\n')
        print(f"[frames] 已写 {out_csv}（{len(rows)} 帧）")
    else:
        print(header)
    for f, nom, bt, bd, st in rows:
        line = (f'{f},{nom if nom is not None else ""},'
                f'{bt if bt is not None else ""},{bd}')
        if by_name:
            line += f',{st}'
        print(line)
    return 0


# ---------------------------------------------------------------- main

def main(argv):
    p = argparse.ArgumentParser(prog='course_verify.py')
    sub = p.add_subparsers(dest='cmd', required=True)

    pw = sub.add_parser('wc', help='字数/单点/图解读/status/禁用词/图片概要')
    pw.add_argument('ep_dir')

    pr = sub.add_parser('refs', help='图片引用逐条解析 + 孤图 + 旧文件名残留')
    pr.add_argument('ep_dir')

    pf = sub.add_parser('frames', help='抽帧真实时刻测量 → frames.csv')
    pf.add_argument('video')
    pf.add_argument('frames_dir')
    pf.add_argument('--interval', type=float, default=None,
                    help='间隔模式（旧帧）：帧间隔（秒），如 10 或 20')
    pf.add_argument('--by-name', action='store_true',
                    help='按名模式（新规范）：文件名即真实秒（f_145.png→145）')
    pf.add_argument('--out', default=None, help='frames.csv 输出路径')

    args = p.parse_args(argv)

    if args.cmd == 'wc':
        return cmd_wc(args.ep_dir)
    if args.cmd == 'refs':
        return cmd_refs(args.ep_dir)
    if args.cmd == 'frames':
        if args.by_name and args.interval is not None:
            print("[frames] --by-name 与 --interval 互斥，二选一")
            return 1
        if not args.by_name and args.interval is None:
            print("[frames] 需 --interval N（间隔模式）或 --by-name（按名模式）")
            return 1
        return cmd_frames(args.video, args.frames_dir, args.interval,
                          args.out, args.by_name)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
