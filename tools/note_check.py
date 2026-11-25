#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""note_check.py —— 讲义自检（一次跑完，替代每次贴 heredoc）

背景（2026-11-22）：ep08 那轮「改稿+复查+台账」用了 102 步 / 20.1M token，
根因是**每步都重放整段上下文（末段约 190k）**，而自检被拆成 3 个 heredoc、
字数被卡死在 4800 导致 32 次 edit 反复压字。本脚本把这堆检查收成一条命令。

判定口径（与 course_verify.py 一致，另加容差带）：
  字数预算 = knowledge_points × 400
  合格带 = [0, 预算 + --tol]（默认 +800）   ← 2026-11-22 用户定：带宽内算合格
  单点正文 ≤ 400 + --kp-tol（默认 40，超出算 warn，超 400+80 算 fail）
  图后逐点解读 ≤ 120 + 20

用法：
  py tools/note_check.py <集目录> [--tol 800] [--json]
  py tools/note_check.py <集目录> --frames-csv <frames.csv>

退出码：0 合格（warn 不算失败）；1 有 fail；2 用法/文件错。
"""
import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import course_verify as cv  # noqa: E402  复用权威口径，别另写一套

REQ_FM = ['course', 'episode', 'topic', 'presentation', 'discipline',
          'evidence', 'figures', 'status', 'review', 'updated']
# 旧格式讲义没有 presentation/discipline 两列，按「降级为 warn」处理
REQ_FM_SOFT = ['presentation', 'discipline']
FIGCAP = re.compile(r'^>\s*图\s*(\d+)\s*[··.]')


def _cjk(s):
    return len(re.findall(r'[\u4e00-\u9fff]', s))


def fig_numbers(lines):
    """正文里 `> 图 N ·` 的图号序列（按出现顺序）"""
    out = []
    for s in lines:
        m = FIGCAP.match(s.strip())
        if m:
            out.append(int(m.group(1)))
    return out


def img_blocks(lines):
    """每张 ![..](..) 后面跟的图注/解读块起止，用于「每张图都被讲解」检查"""
    n = len(lines)
    blocks = []
    for i, s in enumerate(lines):
        if not s.strip().startswith('!['):
            continue
        j = i + 1
        buf = []
        while j < n and not lines[j].strip().startswith(('#', '![')):
            buf.append(lines[j])
            j += 1
        blocks.append((i + 1, '\n'.join(buf)))
    return blocks


def check_fig_seq(nums):
    """图号必须 1..N 连续、不重复、不跳号"""
    problems = []
    if not nums:
        return []
    for i, n in enumerate(nums, 1):
        if n != i:
            problems.append(f'第 {i} 张图注编号写成「图 {n}」（应为 {i}）')
    dup = {n for n in nums if nums.count(n) > 1}
    if dup:
        problems.append(f'图号重复：{sorted(dup)}')
    return problems


def main(argv):
    ap = argparse.ArgumentParser(prog='note_check.py')
    ap.add_argument('ep_dir')
    ap.add_argument('--tol', type=int, default=800,
                    help='总字数容差带（默认 +800，带宽内算合格）')
    ap.add_argument('--kp-tol', type=int, default=40,
                    help='单点正文容差（默认 +40 到 warn，+80 到 fail）')
    ap.add_argument('--frames-csv', default=None,
                    help='可选：course_verify.py frames 产出的 frames.csv，核残差')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    a = ap.parse_args(argv)

    ep = a.ep_dir
    parts = cv.lecture_files(ep)
    if not parts:
        print(f'[note_check] 找不到 {ep}\\10-讲义*.md')
        return 2

    # ---- 分篇支持（2026-11-25，skill 闸门 T1）
    # 以前只认 `10-讲义.md`，拆成 上/下 之后本脚本**一句话都不报**——
    # 它只检查它找到的那个文件，找不到就退 2，看起来像"没问题"。
    # 现在：frontmatter 取第一个非空的，正文按篇顺序拼接后统一统计，
    # 图号连续性也因为拼接而自动跨篇检查。
    fm = cv.merged_lecture_fm(ep)      # 分篇时 knowledge_points 取各篇并集
    nkp = cv.count_knowledge_points(fm)
    budget = nkp * 400
    upper = budget + a.tol
    body = '\n'.join(cv.strip_frontmatter(io.open(p, encoding='utf-8').read())
                     for p in parts)
    txt = '\n'.join(io.open(p, encoding='utf-8').read() for p in parts)
    lines = body.split('\n')

    total, _duo, _cat = cv._count_body_cjk(lines)

    # ---- 逐点字数（沿用 course_verify 的切分逻辑，按其口径重算一遍）
    # 切段时跳过代码块/公式块内的行：块里若有 `### K<N>` 会被误判成新段，导致
    # 单点字数错分（2026-11-24 审查发现；_count_body_cjk 已排除这些块，这里也要对齐）。
    sections, cur_k, cur_name, cur_lines = [], None, '', []
    in_code = in_formula = False
    for s in lines:
        t = s.strip()
        if in_code:
            if t.startswith('```'):
                in_code = False
            continue
        if in_formula:
            if t.startswith('$$'):
                in_formula = False
            continue
        if t.startswith('```'):
            in_code = True
            continue
        if t.startswith('$$'):
            if not (t.endswith('$$') and len(t) > 4):
                in_formula = True
            continue
        if t.startswith('###') or t.startswith('##'):
            m = re.search(r'K(\d+)', t)
            if m:
                nk = int(m.group(1))
                if nk != cur_k:
                    if cur_k is not None:
                        sections.append((cur_k, cur_name, cur_lines))
                    cur_k, cur_name, cur_lines = nk, t, []
                else:
                    cur_name = t
            elif t.startswith('##'):
                if cur_k is not None:
                    sections.append((cur_k, cur_name, cur_lines))
                cur_k, cur_lines = None, []
        elif cur_k is not None:
            cur_lines.append(s)
    if cur_k is not None:
        sections.append((cur_k, cur_name, cur_lines))
    per = []
    for k, name, ls in sections:
        b, d, _ = cv._count_body_cjk(ls)
        per.append((k, name, b, d))

    errs, warns = [], []
    info = {}
    if len(parts) > 1:
        info["parts"] = [os.path.basename(p) for p in parts]

    # ---- 1. 总字数（带宽）
    info['cjk'] = total
    info['budget'] = budget
    info['upper'] = upper
    if total > upper:
        errs.append(f'字数 {total} 超出合格带上限 {upper}（预算 {budget} + 容差 {a.tol}），'
                    f'需砍 {total - upper} 字')
    elif total > budget:
        warns.append(f'字数 {total} 在容差带内（预算 {budget} +{total - budget}，'
                     f'上限 {upper}）→ 合格')
    if nkp == 0:
        errs.append('frontmatter knowledge_points 为空或解析不到 K<N>_ 项')

    # ---- 2. 单点预算
    over_kp_fail = [(k, b) for k, _n, b, _d in per if b > 400 + a.kp_tol * 2]
    over_kp_warn = [(k, b) for k, _n, b, _d in per
                    if 400 < b <= 400 + a.kp_tol * 2]
    info['max_kp'] = max((b for _k, _n, b, _d in per), default=0)
    if over_kp_fail:
        errs.append(f'单点正文超 400+{a.kp_tol * 2}：{over_kp_fail}')
    elif over_kp_warn:
        warns.append(f'单点正文在 400 容差内：{over_kp_warn}')

    # ---- 2b. knowledge_points 清单与正文 K 段对账（warn，不阻塞老集）
    # 清单写 5 个点、正文只写 4 段时，字数预算仍按 5 算，会「假通过」。
    declared = [int(x) for x in re.findall(r'K(\d+)_', fm.get('knowledge_points', ''))]
    body_k = [k for k, _n, _ls in sections]
    if declared and set(declared) != set(body_k):
        miss = sorted(set(declared) - set(body_k))
        extra = sorted(set(body_k) - set(declared))
        warns.append('knowledge_points 清单与正文 K 段不一致：'
                     f'清单有正文无={miss or "无"}，正文有清单无={extra or "无"}')

    # ---- 3. 图后逐点解读
    over_duo = [(k, d) for k, _n, _b, d in per if d > 140]
    info['max_duodian'] = max((d for _k, _n, _b, d in per), default=0)
    if over_duo:
        warns.append(f'图后逐点解读 >140：{over_duo}')

    # ---- 4. 禁用词
    hit = {w: txt.count(w) for w in cv.BANNED if txt.count(w) > 0}
    if hit:
        errs.append(f'禁用词命中：{hit}')

    # ---- 5. 图片引用
    refs, assets, broken, orphan = cv.scan_images(ep)
    info['refs'] = len(refs)
    info['assets'] = len(assets)
    if broken:
        errs.append('坏引用（引用了但盘上没有）：' + '; '.join(f'{r}({s})' for r, s in broken))
    if orphan:
        warns.append('孤图（assets 有但正文没引用）：' + '; '.join(orphan))

    # ---- 6. 图号连续性（两种约定都兼容）
    #   旧格式：![..](assets/x.png) 后跟 `逐点解读：` / `① **…**` 讲解块，无 `> 图 N`
    #   ep05/ep08：再加一行 `> 图 N · 来源…` 图注
    figs = fig_numbers(lines)
    info['figs_caption'] = len(figs)
    blocks = img_blocks(lines)
    info['figs'] = len(blocks)
    fig_problems = check_fig_seq(figs)
    if fig_problems:
        errs.extend(fig_problems)
    if figs and len(figs) != len(blocks):
        warns.append(f'图注 {len(figs)} 个但图片 {len(blocks)} 张，数量不一致')
    # 每张图都必须有讲解块（≥30 个汉字，否则等于只贴图）
    naked = [ln for ln, blk in blocks if _cjk(blk) < 30]
    if naked:
        errs.append('图片下方讲解不足 30 汉字（只贴图不算讲）：正文第 %s 行'
                    % ", ".join(str(x) for x in naked))

    # ---- 6b. 图注时间 vs 文件名时间（2026-11-23 ep14 定）
    #   文件名里 `ep14-t1400` 的 1400 有两种约定，历史产物两种都有：
    #     A) 秒数       t1400 = 第 1400 s = 23:20   （ep14 起，course_run.py shots）
    #     B) MMSS       t0920 = 09:20              （ep12/ep13 老产物）
    #   两种都试，任一对上就算通过；都对不上才 warn。误差容差 2 s。
    timearg = []
    img_lines = [(i, s) for i, s in enumerate(lines) if s.strip().startswith('![')]
    for idx, (i, s) in enumerate(img_lines):
        m = re.search(r'assets/[^)\s]*?[tT](\d{3,5})', s)
        if not m:
            continue
        raw = m.group(1)
        # 该图后面第一个 `> 图 N ·` 行
        cap = None
        for j in range(i + 1, min(i + 6, len(lines))):
            if FIGCAP.match(lines[j].strip()):
                cap = lines[j]
                break
        if cap is None:
            continue
        mm = re.search(r'(\d{1,2}):([0-5]\d)', cap)
        if not mm:
            continue
        csec = int(mm.group(1)) * 60 + int(mm.group(2))
        cand = [int(raw)]                       # A) 秒数
        if len(raw) in (3, 4):                  # B) MMSS
            cand.append(int(raw[:-2]) * 60 + int(raw[-2:]))
        if not any(abs(csec - c) <= 2 for c in cand):
            timearg.append((idx + 1, raw, f'{mm.group(1)}:{mm.group(2)}', cand))
    info['fig_time_mismatch'] = len(timearg)
    if timearg:
        warns.append('图注时间与文件名时间对不上（第几张图, 文件名串, 图注时间, 两种可能的秒数）：'
                     + '; '.join(f'#{a}({b} vs {c}, 试过{d})' for a, b, c, d in timearg))

    # ---- 7. frontmatter 必填
    miss = [k for k in REQ_FM if not fm.get(k)]
    soft = [k for k in miss if k in REQ_FM_SOFT]
    hard = [k for k in miss if k not in REQ_FM_SOFT]
    if hard:
        errs.append(f'frontmatter 缺字段：{hard}')
    if soft:
        warns.append(f'frontmatter 缺字段（2026-11-21 前的老格式，可补）：{soft}')

    # ---- 8. status 与复查表一致性
    st = fm.get('status', '')
    rv = fm.get('review', '')
    if st not in ('draft', 'reviewed'):
        errs.append(f'status 只能是 draft/reviewed，当前「{st}」')
    if st == 'reviewed':
        rvp = os.path.join(ep, rv) if rv else ''
        if not rvp or not os.path.isfile(rvp):
            errs.append(f'status=reviewed 但复查表不存在：{rv}')
        else:
            rt = io.open(rvp, encoding='utf-8').read()
            has_pass = re.search(r'结论[:：]\s*\**\s*通过', rt)
            has_reject = re.search(r'结论[:：]\s*\**\s*(不通过|未通过|打回|驳回)', rt)
            if not has_pass:
                errs.append(f'status=reviewed 但 {rv} 里没有「结论：通过」')
            elif has_reject:
                errs.append(f'status=reviewed 但 {rv} 里出现否决结论'
                            f'（不通过/未通过/打回/驳回），与「通过」冲突')
    if st == 'draft':
        warns.append('status=draft：还没复查通过，不算完成')

    # ---- 9. 可选：frames.csv 残差
    if a.frames_csv:
        if not os.path.isfile(a.frames_csv):
            errs.append(f'找不到 frames.csv：{a.frames_csv}')
        else:
            bad = []
            n = 0
            with io.open(a.frames_csv, encoding='utf-8') as f:
                for ln in f:
                    p = ln.rstrip('\n').split(',')
                    if len(p) < 4 or p[0].startswith('#'):
                        continue
                    try:
                        resid = float(p[3])
                    except ValueError:
                        continue
                    n += 1
                    if resid >= 2.0:
                        bad.append((p[0], resid))
            info['frames_checked'] = n
            if bad:
                errs.append(f'{len(bad)} 帧残差 ≥2s（前 5）：{bad[:5]}')
            elif n == 0:
                errs.append('frames.csv 里没解析到数据行')

    # ---- 输出
    res = dict(ok=not errs, errors=errs, warnings=warns, info=info)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if not errs else 1

    print(f'== note_check: {os.path.basename(ep.rstrip(os.sep))} ==')
    band = f'≤{upper} 合格（预算 {budget} + 容差 {a.tol}）'
    print(f'字数: {total} / {band}，{nkp} 个知识点')
    print(f'单点正文最大: {info["max_kp"]}    图后解读最大: {info["max_duodian"]}')
    print(f'图片: 引用 {info["refs"]} 处 / assets {info["assets"]} 文件 / '
          f'图片 {info["figs"]} 张（其中带 `> 图 N` 图注 {info["figs_caption"]} 张）')
    if a.frames_csv:
        print(f'帧核验: {info.get("frames_checked", 0)} 帧（残差阈值 2.0）')
    for e in errs:
        print(f'[FAIL] {e}')
    for w in warns:
        print(f'[warn] {w}')
    print(f'结论: {"PASS" if not errs else "FAIL"}'
          f'（{len(errs)} 错 / {len(warns)} 警）')
    return 0 if not errs else 1


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))
