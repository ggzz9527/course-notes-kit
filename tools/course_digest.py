#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""course_digest.py —— 把整集转写压成「知识点候选卡」，再交给会话模型

为什么要这个（2026-11-22 实测）：
  ep06 那轮把 1259 段转写（3.5 万 token）整段读进会话，会话基线从 29.8 万涨到 35.2 万，
  此后**每一步都在重放**这 5.4 万增量。转写真正有用的信息不到 1/5，剩下全是
  "对不对 / 看到吗 / 没错吧 / 这个这个这个"。

两种模式：
  digest  纯机械（默认，0 成本、可复现）
      1) 去口头禅与重复回声；2) 按 --chunk 秒切块；3) 每块留"带术语的原话"；
      4) 输出知识点候选行（时间戳 + 首句 + 术语命中），供会话模型决定读哪几块原文。
      实测压缩到 87% —— **只够查证，不够替代原文**。
  digest --llm
      在上面基础上调 tools/llm.py（默认 glm-4-flash）分 3 段做二次摘要，
      输出 10~15 条知识点候选卡，约 900 token。

⚠️ 已知缺陷（2026-11-22 ep08 实测，别当没看见）：
  1) 摘要**继承转写错字且不加标注**："恒流区"→"横流区"、"绝缘栅型"→"绝缘山形"；
  2) 分段调用会**整段丢内容**：ep08 摘要从 10:07 直接跳到 18:12，中间 8 分钟缺失。
  ⇒ 摘要只当"目录 + 时间戳索引"用；写讲义前必须拿时间戳回 `ts.txt` 核对原文。

用法：
  py tools/course_digest.py <媒体目录>                  # → digest.md（机械）
  py tools/course_digest.py <媒体目录> --llm            # → digest-llm.md（再压一层）
  py tools/course_digest.py <媒体目录> --chunk 120 --terms "PN结,扩散,复合"
  py tools/course_digest.py <媒体目录> --stats          # 只打压缩比，不写文件

输出：
  <媒体目录>/digest.md         机械压缩稿（带时间戳，可直接喂给会话模型）
  <媒体目录>/digest-llm.md     二次摘要稿（--llm 时）
退出码：0 成功；1 失败。
"""
import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(ROOT)

# 口头禅 / 回声（老师上课高频，删掉不损失信息）。
# 只放**多字固定短语**，绝不放单字（"好/啊/呢/嗯/哎"）或双字母（"ok"）：
# 单字用全局 replace 会把"良好→良"、"book→bo"这种正常词一起毁掉。
FILLER = [
    "对不对", "对对吧", "看到吗", "看到了吗", "看到吧", "没错吧", "是吧", "对吧",
    "同学们", "大家可以看到", "大家看", "我们来看", "来看一看", "我们来看看",
    "这个这个", "这个这个这个", "那那那", "那个那个",
]
# 只由这些字构成的句子整句删
PURE_FILLER = re.compile(
    r'^[\s，。、？！：；（）"\'"''…—' + ''.join(FILLER) + r']*$')
SENT_SPLIT = re.compile(r'[。？；\n]+')
TECH_HINT = re.compile(
    r'[A-Za-z]{2,}|[ββαΔ]|浓度|掺杂|扩散|漂移|复合|产生|结|区|极|电压|电流|'
    r'偏置|正偏|反偏|导通|截止|放大|系数|电阻|电容|频率|增益|反馈|饱和|'
    r'特性|曲线|模型|等效|电路|电源|接地|负载|输出|输入')


def load_srt(path):
    """→ [(秒, 文本)]"""
    txt = io.open(path, encoding='utf-8').read().strip()
    out = []
    for blk in txt.split('\n\n'):
        ls = blk.split('\n')
        if len(ls) < 3:
            continue
        m = re.search(r'(\d+):(\d+):(\d+)[,.](\d+)', ls[1])
        if not m:
            continue
        h, mi, s, ms = (int(x) for x in m.groups())
        t = h * 3600 + mi * 60 + s + ms / 1000
        out.append((t, ' '.join(x.strip() for x in ls[2:])))
    return out


def clean(s):
    """去口头禅、去重复回声、去纯填充句"""
    if PURE_FILLER.match(s):
        return ''
    # 连续重复的同一短语（"扩散扩散"、"这个这个"）压成一个
    s = re.sub(r'(.{2,4})\1{1,}', r'\1', s)
    for f in sorted(FILLER, key=len, reverse=True):
        s = s.replace(f, '')
    s = re.sub(r'[，、]{2,}', '，', s)
    s = re.sub(r'^[，、。\s]+', '', s).strip()
    if len(re.findall(r'[\u4e00-\u9fff]', s)) < 4:
        return ''
    return s


def build_chunks(segs, chunk_sec):
    """按时间切块；每块内句子去重（同一句念两遍只留一次）"""
    if not segs:
        return []
    t0 = segs[0][0]
    chunks = {}
    for t, raw in segs:
        k = int((t - t0) // chunk_sec)
        chunks.setdefault(k, []).append((t, raw))
    out = []
    for k in sorted(chunks):
        rows = chunks[k]
        seen, kept = set(), []
        for t, raw in rows:
            c = clean(raw)
            if not c:
                continue
            key = re.sub(r'[^\u4e00-\u9fffA-Za-z]', '', c)[:24]
            if key in seen:
                continue
            seen.add(key)
            kept.append((t, c))
        out.append(dict(start=rows[0][0], end=rows[-1][0], rows=kept))
    return out


def score(rows):
    """这一块里有多少句带术语（信息密度）"""
    return sum(1 for _t, c in rows if TECH_HINT.search(c))


def emit_md(chunks, title, src):
    L = [f'# {title} · 转写摘要', '',
         f'> 由 `tools/course_digest.py` 生成，来源 `{src}`。',
         '> 用法：先读本文件定知识点骨架，需要细节时再按时间戳回查 `ts.txt` / `transcript.srt`。',
         '> 已删除口头禅与重复回声；**保留时间戳**，任何时候都能回原文核对。', '',
         '## 分块索引（先看这张表，决定读哪几块）', '',
         '| 块 | 时间 | 帧候选 | 句数 | 术语句 | 首句（这一块一开始在讲什么） |',
         '| --- | --- | --- | --- | --- | --- |']
    for i, c in enumerate(chunks, 1):
        first = c['rows'][0][1][:46] if c['rows'] else '（空）'
        L.append('| %d | %s–%s | %s | %d | %d | %s |' % (
            i, hhmm(c['start']), hhmm(c['end']),
            int(c['start']) // 10 * 10, len(c['rows']), score(c['rows']), first))
    L.append('')
    for i, c in enumerate(chunks, 1):
        L.append(f'## 块 {i} · {hhmm(c["start"])}–{hhmm(c["end"])}')
        L.append('')
        for t, s in c['rows']:
            L.append(f'- `{hhmm(t)}` {s}')
        L.append('')
    return '\n'.join(L)


def hhmm(t):
    return '%02d:%02d' % (int(t) // 60, int(t) % 60)


def cjk(s):
    return len(re.findall(r'[\u4e00-\u9fff]', s))


def main():
    ap = argparse.ArgumentParser(prog='course_digest.py')
    ap.add_argument('media_dir', help='tools/media/course/<BV>-ep<N>/p<N>')
    ap.add_argument('--chunk', type=int, default=120, help='切块秒数（默认 120）')
    ap.add_argument('--title', default=None)
    ap.add_argument('--llm', action='store_true', help='再做一层 LLM 摘要')
    ap.add_argument('--stats', action='store_true', help='只打压缩比')
    a = ap.parse_args()

    d = a.media_dir if os.path.isabs(a.media_dir) else os.path.join(PROJ, a.media_dir)
    if not os.path.isdir(d):
        # 兼容两种写法：从 tools/ 起算、或从仓库根起算
        alt = os.path.join(ROOT, a.media_dir)
        if os.path.isdir(alt):
            d = alt
    srt = os.path.join(d, 'transcript.srt')
    if not os.path.isfile(srt):
        print(f'[digest] 找不到 {srt}')
        return 1
    segs = load_srt(srt)
    if not segs:
        print('[digest] srt 解析为空')
        return 1

    chunks = build_chunks(segs, a.chunk)
    title = a.title or os.path.basename(os.path.dirname(d))
    md = emit_md(chunks, title, os.path.relpath(srt, PROJ))

    raw_cjk = sum(cjk(x) for _t, x in segs)
    dig_cjk = cjk(md)
    print(f'[digest] {len(segs)} 段 / {hhmm(segs[-1][0])} → {len(chunks)} 块')
    print(f'[digest] 正文汉字 {raw_cjk} → 摘要汉字 {dig_cjk}'
          f'（压缩到 {dig_cjk / max(raw_cjk, 1) * 100:.0f}%）')
    print(f'[digest] 字数 {len(md)} 字符 ≈ {len(md) // 2} token（粗估，中文）')
    if a.stats:
        return 0

    out = os.path.join(d, 'digest.md')
    io.open(out, 'w', encoding='utf-8').write(md)
    print(f'[digest] 写出 {os.path.relpath(out, PROJ)}')

    if a.llm:
        rc = llm_pass(md, d, title)
        if rc:
            return rc
    return 0


def llm_pass(md, d, title):
    """调 tools/llm.py 做二次摘要。分段调用再合并（单次输出会截断）。"""
    sys.path.insert(0, ROOT)
    try:
        import llm  # noqa: E402
    except Exception as e:                                    # noqa: BLE001
        print(f'[digest] 跳过 LLM：import llm 失败 {e}')
        return 0
    if not llm.available():
        print('[digest] 跳过 LLM：tools/llm.json 没配 api_key')
        return 0

    sysp = ("你是模拟电子技术课的助教，擅长把课堂口语转写压成结构化知识点卡。"
            "只依据给定材料，不编造，不逐句抄。")

    def ask(block, seg_note, lo, hi):
        prompt = (
            f'下面是这节课**第 {lo}–{hi} 分钟**这一段的课堂转写，'
            f'已去口头禅、按 2 分钟分块。\n'
            f'请整理成这一段的**知识点候选卡**，严格遵守：\n\n'
            f'1. 输出 **{seg_note} 条**，只覆盖给定的这段时间，不要编后面的内容。\n'
            '2. 格式固定：`- [起止时间] 知识点名 ｜ 这一段在讲什么（1–2 句，说清机制）｜ 术语1、术语2、术语3`\n'
            '3. **必须合并同类**：同一个概念讲了 5 分钟只出 1 条，时间写成跨度（如 `18:40–21:50`）。\n'
            '4. 一个知识点＝一个能独立出题的最小单元；不要把两个概念塞一条，也不要拆成六条。\n'
            '5. 布置作业、纪律、闲聊、"这个这个"全部丢掉。\n'
            '6. **不许编造**；原文没讲清的，末尾加「（原片未展开）」。\n'
            '7. 术语要具体（写 `发射结正偏` 不写 `偏置`；写 `I_CBO` 不写 `电流`）。\n\n'
            '反面例子（不要）：`- 12:22 ic ｜ 集电极电流 ｜ ic、集电极`（逐句抄）\n'
            '正面例子（要）：`- 18:40–21:50 R_B 为什么不能省 ｜ 直接接 V_BB 会把管子烧掉，'
            '串 R_B 后 I_B 上限被钉死在 V_BB/R_B ｜ R_B、限流、发射结`\n\n'
            '--- 材料开始 ---\n' + block + '\n--- 结束 ---'
        )
        return llm.chat([{"role": "system", "content": sysp},
                         {"role": "user", "content": prompt}],
                        temperature=0.2, max_tokens=2000, timeout=300)

    # 按 "## 块 N" 切成大段：每段约 1/3 集
    parts = re.split(r'\n(?=## 块 \d+)', md)
    head, blocks = parts[0], parts[1:]
    n_seg = 3 if len(blocks) >= 12 else 2
    per = (len(blocks) + n_seg - 1) // n_seg
    got, fails = [], 0
    for i in range(n_seg):
        grp = blocks[i * per:(i + 1) * per]
        if not grp:
            continue
        lo, hi = i * (47 // n_seg), (i + 1) * (47 // n_seg)
        note = '4–5' if len(grp) >= 6 else '2–3'
        print(f'[digest] LLM 第 {i+1}/{n_seg} 段（{lo}–{hi} 分，{len(grp)} 块）...')
        try:
            t = ask('\n'.join(grp), note, lo, hi)
        except Exception as e:                                # noqa: BLE001
            print(f'[digest] 第 {i+1} 段失败：{e}')
            fails += 1
            continue
        if not t or not t.strip():
            print(f'[digest] 第 {i+1} 段返回空')
            fails += 1
            continue
        got.append(t.strip())
    if not got:
        print('[digest] 全部段失败，保留机械稿')
        return 0
    body = '\n'.join(got)
    out = os.path.join(d, 'digest-llm.md')
    io.open(out, 'w', encoding='utf-8').write(
        f'# {title} · 知识点候选卡（LLM 二次摘要）\n\n'
        f'> 由 `tools/llm.py`（{llm.load_conf().get("model")}）分 {n_seg} 段生成后合并；'
        f'机械稿见 `digest.md`。**这是候选，不是结论**——写讲义前必须回原文核对。\n'
        f'> ⚠️ 分段调用，段与段之间可能有 1–2 条重叠或边界遗漏，据时间戳自行去重。\n\n'
        + body + '\n')
    print(f'[digest] 写出 {os.path.relpath(out, PROJ)}（{cjk(body)} 汉字，{fails} 段失败）')
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.exit(main())
