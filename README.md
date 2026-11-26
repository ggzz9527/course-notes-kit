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
├── skills/course-selflearn/     讲义怎么写（SKILL.md + 5 篇 references）
├── preset/                      省 token 的 agent preset（可选）
├── docs/                        流程、安装、踩坑、插件化决策
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

装完先自检一遍：

```bash
python scripts/selfcheck.py
```

判据：`通过 7 项 / 警告 1 项 / 失败 0 项` 是正常态（那条警告是「没有 tools/llm.json」，
只影响 `course_digest.py --llm`）。有 `失败` 就先修，别往下走。

**注意解释器**：`selfcheck` 查的是「**跑它的那个解释器**有没有依赖」。
本机如果在 `python` 和装了 `yt-dlp` 的 venv 之间有两个，就得用后者跑——
否则会看到 `4 通过 / 2 警告 / 1 失败`（缺 yt-dlp），那是环境错配，不是脚本退化。
判据要连同解释器一起说：`<装了依赖的解释器> scripts/selfcheck.py` → 7/1/0。

改过抽帧相关代码、或发版之前，再跑一遍抽帧回归：

```bash
python scripts/regress_frames.py
```

判据：`通过 6 项 / 失败 0 项`。它守的是一条用状态机验证过的判据——
**不存在「报就绪但帧属于别的视频」的可达状态**。每个 FAIL 场景都对应一个真实发生过的谎报
（换视频后拿旧帧冒充、同长度换画面不重抽、无身份证的老目录直接继承）；
自建临时目录、跑完自动删，不动你自己的素材。

改过下载相关代码（`course_prep.py` 的 `download()` / `resolve_urls()` / `pick_format()`）
或发版之前，再跑下载回归：

```bash
python scripts/regress_download.py
```

判据：`通过 8 项 / 失败 0 项`。它守的是同一条不变量的下载版本——
**不存在「报成功但落盘文件是残的」的可达状态**。八个场景各对应一种真实失效：
206 少给一段、`Content-Length` 与实际 body 不符、mirror 全挂但原始直链可用、
mirror 与直链全挂、完整文件重跑不重下、半截文件不许继承，
外加两条 `--keep`：已有完整整集时必须真的裁出片段（不重下），
残整集时必须重下再裁、且不把整集留在盘上。

判据落在**成品**上：体积猜不出来（低码率集和残件都能「够大」），`ffmpeg -c copy`
对截断流也可能返回 0，只看 rc 必漏。每次成功都要 `ffprobe` 读一次时长、与
`meta.json` 的 `duration` 比对（容差 1 s），并与上次核过的字节数
（`<文件>.id.json`）一致。
全程本地伪造响应（能指定状态码/分段长度/`Content-Length` 的 `http.server`）、
不联网——真去连 B 站，判据会随对方抽风，换个节点就从全绿变成一堆红。

为什么两个回归都不并进 `selfcheck.py`：selfcheck 的契约是「不联网、不下载、**不改任何文件**」，
而这两个回归都必须真写文件——并进去就破了它的契约，下一个会话会不敢跑 selfcheck。

三个闸门脚本都支持 `--json`（退出码不变，只加信息）：

```bash
python scripts/selfcheck.py --json
python scripts/regress_frames.py --json
python scripts/regress_download.py --json
```

```json
{"ok": true, "script": "regress_download", "root": "...", "rev": "e1a91ea",
 "total": 8, "failed": 0, "warnings": 0,
 "results": [{"status": "ok", "ok": true,
              "name": "A 206 少给一段 → exit 1、不落盘", "detail": "..."}]}
```

每条结果是三态 `status`（`ok` / `warn` / `fail`）；`ok` 字段的含义是**没失败**，
所以 `warn` 既不会让 `ok` 变 false，也不会让退出码变 1——**警告不算失败**
（selfcheck 的正常态就带一条警告）。

进度行在 `--json` 下走 stderr，所以 `--json 2>/dev/null` 拿到的是纯 JSON；
崩了也照样给结论（`{"ok": false, ..., "error": "TypeError: ..."}`），不让调用方去猜 traceback。

**为什么结果里要带 `root` 和 `rev`**：判据最危险的失效方向不是误报 FAIL，是在**错的树上**
误报 PASS——在别人 fork、旧版本目录、或自己的开发副本上跑一遍，然后签字说「发版前验过了」。
把跑的是哪棵树写进结果，读的人一眼就能对上。`rev` 是直接读 `.git/HEAD` 拿的（不调 `git`）：
zip 分发的包没有 `.git`，就老实写 `unknown`——自证是描述，不是闸门。

## 让 skill 出现在每个会话（可选，装成 DSH 插件）

`skills/course-selflearn/` 是「本机有 clone」时用的。要让它在**每个会话**的技能目录里
都出现、不必手工拷进 `<工作区>/.dsh/skills/`，就把 skill 装成一个小 bundle——
它只注册一个 skill provider，**不注册工具、不挂 hook**，所以每轮的固定开销一点不变。

安装、验证、回滚见 `docs/02-安装-preset.md` 第六节。两条判据里第二条最容易被漏：

- skill 出现在目录里；**并且**它报的资源基址指向**插件包内**的 `skills/course-selflearn/`。

只看第一条不算——手工副本还在的话，技能注册按「近的层赢」，插件没生效也照样能用。

为什么插件里放的是**逐字副本**而不是软链或直接读本仓库：软链与绝对路径都会把
「kit 装在哪」焊死，正好违反 `SKILL.md` §七 的可移植条款。

## 配置

**本工具链不绑定任何机器路径**，全部走环境变量（都有默认值）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `COURSE_MEDIA` | `./tools/media/course` | 素材根目录（视频/音频/帧，不入库） |
| `COURSE_OUT` | `./course-notes` | 讲义产物根目录 |
| `COURSE_NOTES` | 空 | 已有讲义库根目录（给了就去这里找 `_course.json`） |
| `COURSE_MEDIA_DIR` | 空 | 指定某一集的媒体目录（`course_gate` 找 `shots.log` 用） |
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
（skill 本身的安装是另一件事：见上面「让 skill 出现在每个会话」——两件事共用 `docs/02`，但互不依赖。）

## 许可

工具与文档以 MIT 分发。抽帧得到的课堂画面版权归原课程作者，本仓库不打包任何课程媒体。
