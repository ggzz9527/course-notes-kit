# course-notes-kit —— 把课程视频整理成可复查的图文讲义

一套「拉课 → 转写 → 出知识点卡 → 核数字 → 挑帧 → 写讲义 → 机械自检 → 复查 → 台账」的可执行工具链，
外加一个**省 token 的专用 agent preset**（同一个 DSH 会话里，每轮固定开销从约 7 万 token 压到 8 千以内）。

不是"AI 帮你总结课程"——是**逼着每一步都能指回原片证据**的讲义生产线：

- 讲义里每个电压、系数、判据式，都要能指到 `transcript.srt` 的行号；
- 每张图必须是原片截图（裁切放大后的），并标出处时刻；
- 出稿先过 `note_check.py`，FAIL 不许标 `reviewed`；
- 视觉模型只准用来**筛选哪一帧值得看**，它的文字描述一律不进正文。

## 它解决什么问题

让 AI 写课程讲义，最容易死在三件事上：

| 症状 | 后果 | 本套工具的应对 |
| --- | --- | --- |
| 转写 3.5 万 token 全塞进上下文 | 之后每一步都重放它，一集课烧掉上千万 token | `course_digest.py` 压缩 + 派子智能体出卡片，原文不进主会话 |
| 数字/符号凭记忆写 | 讲义看起来像真的，但读者没有纠错能力 | 硬规则：数字必须 `grep` 回原文核，卡片上的数字一律不信 |
| 图是网图或凭印象挑的 | 图文对不上，等于误导 | 只允许原片抽帧，图后必须有逐点解读，`note_check` 查孤儿图 |

## 目录

```
course-notes-kit/
├── tools/                       管线脚本（纯 Python 标准库为主）
│   ├── course_prep.py           取 meta / 下音频 / 下视频片段 / 抽帧 / 帧核验
│   ├── course_run.py            fetch / shots / check / rank / digest 一条命令
│   ├── transcribe.py            faster-whisper 转写（可降级为外部字幕）
│   ├── course_verify.py         帧残差核验、讲义机械项
│   ├── course_scan.py           批量筛帧：出清单 + 并表成段落表
│   ├── course_digest.py         转写压缩（机械稿 / 可选 LLM 摘要）
│   ├── course_budget.py         字数预算计算
│   ├── note_check.py            讲义自检（9 类）
│   ├── gen_lib.py               生成 00-索引 / 00-课程地图 / 99-台账
│   ├── course_rank_check.py     检验「按 green 分板书/人物」判据是否还成立
│   └── llm.py                   OpenAI 兼容调用（可选，用你自己的模型）
├── skills/course-selflearn/     讲义怎么写（SKILL.md + 4 篇 references）
├── preset/                      省 token 的 agent preset（可选）
├── docs/                        流程、安装、踩坑
└── scripts/                     安装/自检辅助
```

## 依赖

| 必需 | 说明 |
| --- | --- |
| Python 3.10+ | 管线脚本以标准库为主 |
| yt-dlp | 取 B 站 meta 与直链（`pip install yt-dlp`） |
| ffmpeg / ffprobe | 抽帧、切片段、合并音频 |

| 可选 | 用途 |
| --- | --- |
| faster-whisper（+ `av==13.1.0`） | 本地转写；不装就用外部字幕 |
| 任意 OpenAI 兼容模型 | `course_digest.py --llm` 的摘要、题库等 |
| 视觉多模态模型 | 批量筛帧（见 `skills/course-selflearn/SKILL.md` ②-B） |

> `av` 必须 `13.1.0`：`av>=15` 删掉了 faster-whisper 依赖的 `metadata_errors` 参数，会直接 TypeError。

## 安装

```bash
git clone <this-repo> course-notes-kit
cd course-notes-kit
pip install yt-dlp
# 可选：本地转写
pip install faster-whisper "av==13.1.0"
```

ffmpeg：Windows 放 `tools/bin/ffmpeg.exe`，或设环境变量 `COURSE_FFMPEG`，或直接装进 PATH。

## 配置

**本工具链不绑定任何机器路径**，全部走环境变量（都有默认值）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `COURSE_MEDIA` | `./tools/media/course` | 素材根目录（视频/音频/帧，不入库） |
| `COURSE_OUT` | `./course-notes` | 讲义产物根目录 |
| `COURSE_LIB` | 同 `COURSE_OUT` | 已有讲义库（`gen_lib.py` 扫这里） |
| `COURSE_PY` | 当前解释器 | 子进程用的 python |
| `COURSE_FFMPEG` / `COURSE_FFPROBE` | `./bin/*.exe` → PATH | ffmpeg/ffprobe |
| `COURSE_TMP` | `./tools/tmp` | 临时文件（沙箱里不能用管道时靠它中转） |

模型**不写死**：`tools/llm.py` 读 `tools/llm.json`（见 `tools/llm.example.json`），
填你自己的 `base_url / api_key / model`。视觉筛帧用 `workflow` 派子智能体，
provider/model 由你自己的 DSH 配置决定——**本仓库只给选型判据，不指定型号**。

## 课程不同，讲义怎么变（重要）

同一个骨架套所有课会同时犯两种错：数学课用结论式写法，学生背下结论走不出第二步；
电路课用长篇推导，该背的判据没背下来。

这件事**不靠一篇单独的画像文件**，而是钉进每篇讲义自己的 frontmatter——
`presentation` 与 `discipline` 两个字段（缺了 `note_check.py` 会给 warn）：

| 字段 | 取值 | 决定 |
| --- | --- | --- |
| `discipline` | math / ee / physics / cs / humanities | 拓展点、自测题型、学科红线集 |
| `presentation` | ppt / blackboard / mixed / handout | 以谁为准、挑哪一刻截图、图怎么用 |

写的时候按这两条自洽：**学科**决定「红线在哪、自测怎么出」；**呈现方式**决定
「以投影为准还是以板书为准、关键帧挑哪一刻、要不要标「老师跳过」」。
学科红线机器查不出，只能人工过一遍——所以它是判据，不是产物，没有单独文件要维护，
也就不会出现「画像写板书、实际录的是投影」这种自相矛盾。

## 快速开始

```bash
# 1) 一集课的全套素材（meta → 音频 → 转写 → 视频 → 抽帧 → 帧核验）
python tools/course_run.py fetch BV1xx411c7mD 8

# 2) 转写压缩成知识点候选卡（可选 --llm，走你自己的模型）
python tools/course_digest.py tools/media/course/BV1xx411c7mD-ep8/p8 --llm

# 3) 核数字：只读命中行，绝不通读
grep -n "0.3\|毫伏\|翻一倍" tools/media/course/BV1xx411c7mD-ep8/p8/ts.txt
sed -n '455,475p' tools/media/course/BV1xx411c7mD-ep8/p8/ts.txt

# 4) 挑帧落 assets
python tools/course_run.py shots course-notes/ep08-xxx \
  "450:::输入特性" "2280:crop:60,55,720,480:1.6:输出特性曲线"

# 5) 写完讲义后机械自检（退出码 0 才算合格）
python tools/note_check.py course-notes/ep08-xxx --frames-csv tools/media/course/BV1xx411c7mD-ep8/p8/frames.csv

# 6) 重生成索引/课程地图/台账
python tools/gen_lib.py course-notes
```

## 产物长什么样

每集一个目录，frontmatter 带 `presentation`（ppt/blackboard/…）与 `discipline`（math/ee/…），
这两个字段决定讲义骨架、红线集、以及"以投影为准还是以板书为准"。
详见 `skills/course-selflearn/references/单篇模板.md`。

## 省 token 的部分（可选）

`preset/` 是一个专用 agent preset：只挂 20 个工具（通用 preset 有 175 个）。
实测首步基线 74,053 token → 约 8,000（工具 schema 70,700 → 3,633）。

安装方式见 `docs/02-安装-preset.md`。**不装也能用本仓库的脚本和 skill**，只是每轮会重发一份用不到的 schema。

## 许可

工具与文档以 MIT 分发。抽帧得到的课堂画面版权归原课程作者，本仓库不打包任何课程媒体。
