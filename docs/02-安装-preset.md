# 安装 note-keeper preset（可选）

这个 preset 只干一件事：**把每轮固定开销从约 7 万 token 压到 8 千以内**。
它只挂 20 个工具（不挂桌面操控 63 个、浏览器 34 个、记忆库 17 个等笔记流程用不到的东西）。

不装也能用本仓库的脚本和 skill，只是每轮会重发一份用不到的 schema。

## 一、DSH 认哪种写法（唯一生效路径）

只有一条路：在 `~/.dsh/profiles/desktop/cordis.patch.yml` 里，用 **`- insert:`** 包住一个
`- id: preset-xxx` 条目。

两个坑（都实测过）：

1. **裸写 `- id: preset-xxx` 不生效**——boot 会把它当"覆盖某条已存在的行"，找不到目标就 warn + skip；
2. **`~/.dsh/.agent-presets/` 是死路**——没有任何包读这个目录。

## 二、装

### 1. 备份（不备份不许改）

```bash
cp ~/.dsh/profiles/desktop/cordis.patch.yml ~/.dsh/profiles/desktop/cordis.patch.yml.bak
```

### 2. 检查顶层结构

```bash
head -5 ~/.dsh/profiles/desktop/cordis.patch.yml
```

必须是**顶层 YAML 数组**（以 `- id:` 开头）。是字典就别照抄，先改结构。

### 3. 把 `preset/agent.cordis.yml` 的内容内联进 patch

在文件末尾追加：

```yaml
- insert:
    - id: preset-note-keeper
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: note-keeper
        name: 笔记整理
        description: 拉课出讲义专用：只挂 20 个工具
        order: 20
        plugins:
          # ← 把 preset/agent.cordis.yml 里 - id: persona 开始的所有条目原样搬进来
```

注意：**persona 的 prefix 要完整复制**，不要自己缩写；条目的缩进在 `plugins:` 下需要再进一级。

### 4. 语法校验（写坏 DSH 起不来）

```bash
python -c "import io,os,yaml;t=io.open(os.path.expanduser('~/.dsh/profiles/desktop/cordis.patch.yml'),encoding='utf-8').read().replace('!!js','');d=yaml.safe_load(t);ins=[r.get('id') for x in d if 'insert' in x for r in x['insert']];print('OK',len(d),'顶层');print('insert:',ins)"
```

**判据**：能解析，且 `insert:` 列表里出现 `preset-note-keeper`。

⚠️ **只打印顶层 id 会得到假失败**：`- insert:` 包住的条目是嵌套的，`[x.get('id') for x in d]`
在正确安装的情况下也**看不到**它（会打印一个 `None`）。本节第一版就是这么写的，
照它验会让人以为没装上，然后去乱改一个其实没问题的文件。上面那行专门把 `insert`
里的 id 摊平出来。

同理，`$HOME` 在某些 shell 里不展开，所以改用 `os.path.expanduser`。

### 5. 重启 DSH，然后验证

新建会话 → 「模式」下拉里应看到「笔记整理」（如果显示的是 `note-keeper`，说明内联方式只认 `config.id`，
不影响功能）。

**真正的验证**：首轮问它「列一下你有哪些工具」。

- 20 个左右 → ✅ 生效
- 175 个 → ❌ 没选上，回去看下拉里有没有这一项

## 三、失败怎么回滚

```bash
cp ~/.dsh/profiles/desktop/cordis.patch.yml.bak ~/.dsh/profiles/desktop/cordis.patch.yml
```

DSH 起不来时也能用这条在 DSH 外面救回来。

## 四、常见失败原因

| 现象 | 原因 |
| --- | --- |
| 下拉里没有这一项 | 裸写 `- id:` 没被 insert 包住；或某个 `name:` 包名写错 → 整个 preset broken 被过滤 |
| 会话打不开 | persona 挂了两次（与全局 persona 冲突）——不要把插件行当 profile 行内联 |
| 工具是 175 个 | 模式没选对，或 preset 没被 registry 认到 |

排查顺序：`~/.dsh/logs/` 最新日志里搜 `note-keeper` → 核对包名 → 核对 YAML 顶层是数组。

## 五、两件东西，别混：preset 管 token，bundle 管 skill 发现

| 想解决什么 | 用哪个 | 代价 |
| --- | --- | --- |
| 每轮固定开销从 ≈70,700 token 压到 ≈3,633 | **agent preset**（第一～四节） | 改 profile patch；工具白名单固定 |
| 让 skill 出现在**每个会话**的技能目录，不用手工拷 | **一个只注册 skill 的 bundle**（第六节） | 一个 skill provider 行；零工具、零 hook、零 schema |

**为什么不做成「把工具注册进来的 cordis 插件」**：那样每个工具的 schema 会回到
**每一条**会话，正面撞上 preset 自己的设计目标。这条理由没变。

变的是：skill 的**发现**只需要一个 provider 行（复用官方的文件系统 provider），
不必注册任何工具。所以「插件」在这里不再是「要么 preset、要么工具插件」的二选一——
**preset 管 token 预算，bundle 管 skill 发现，两件事分开做。**
反过来也要守住：**不要**为了「装 skill 方便」往 bundle 里塞工具，那会同时破坏
preset 的判据 3 和下面这张表里所有的数。

所以交付判据就是下面四条，缺一条不算装好：

| # | 判据 | 怎么验 |
| --- | --- | --- |
| 1 装上了 | 「模式」下拉有「笔记整理」；首轮工具数 ≈20（不是 175） | 新会话问「列一下你有哪些工具」 |
| 2 自证 | 脚本链在这台机器上真能跑 | `<装依赖的解释器> scripts/selfcheck.py` → 7/1/0；`python scripts/regress_frames.py` → 6/0；`python scripts/regress_download.py` → 8/0；三个对账 + `scrub_kit` 全 exit 0 |
| 3 零运行逻辑 | preset 里没有运行逻辑、不注册新命令、工具白名单固定 | `grep -c '^\s*- id:' preset/agent.cordis.yml` → **21**（期望值写在这里，因为只给方法不给数，数出 22 也没人知道该不该报警）；每加一个工具就重算 token |
| 4 可回滚 | 删掉 patch 里那一段就卸载 | 见上面「三、失败怎么回滚」 |

第 2 条为什么要单列：**preset 装对了不等于流程能跑**。工具白名单对、但脚本缺依赖
（ffmpeg / faster-whisper）时会话看起来正常，一 `fetch` 就炸——那时候不容易判断是
preset 的问题还是环境的问题。先跑 `selfcheck`，把「这台机器能不能用」和
「preset 装没装对」分开验。

## 六、装 skill bundle（可选）

前提：插件包单独分发，不在本仓库里。它只做一件事——把一个 skill provider 注册到
**全局技能层**，把包内 `skills/course-selflearn/`（`SKILL.md` + 5 篇 `references/`）
交给 DSH 的技能目录。**不注册工具、不挂 hook。**

### 1. 先备份（同第二节第 1 步，不备份不许改）

安装会改 profile 的 `package.json`（加一条 link 依赖）与 bundles 列表：

```bash
cp ~/.dsh/profiles/desktop/package.json ~/.dsh/profiles/desktop/package.json.bak
```

### 2. 装

用 DSH 的插件管理器把包目录装进当前 profile。**不要手写 profile 的 `package.json`**：
手写等于绕过它自己的解析与校验。

```
plugin_manager install_bundle target=<插件包的绝对路径>
```

**判据**：返回里 `application` 是 `applied`。只有 `warnings` 不算装上。

### 3. 验（两条，缺一条不算装好）

| # | 判据 | 怎么验 |
| --- | --- | --- |
| 1 | skill 进了每个会话的技能目录 | 开新会话，问「你有哪些 skill」，列表里有 `course-selflearn` |
| 2 | **是插件供的，不是手工副本供的** | 让它报告这个 skill 的资源基址：应指向**插件包内**的 `skills/course-selflearn/` |

第 2 条为什么必须单列：工作区里可能还留着 `<工作区>/.dsh/skills/course-selflearn/`
这份手工副本。技能注册是分层的、**近的层赢**，所以手工副本还在时，插件没生效也照样能用——
只看判据 1 会得到假 PASS。想干净地验，先把手工副本移走再验。

### 4. 装完不该变的数

| 数 | 期望 | 为什么 |
| --- | --- | --- |
| preset 的 `- id:` 行数 | 仍 **21** | bundle 是独立一行，不进 preset |
| 工具数 | 仍约 20（不是 175） | bundle 不注册工具 |
| 工具 schema 体量 | 仍 ≈14,534 字符 ≈ 3,633 token | 同上 |

这三条是「装 skill 的插件没把 token 预算偷走」的判据。任何一条变大，就说明它塞了工具进来。

### 5. 回滚（删掉即退回原行为）

1. 从 profile 的 bundles 列表移除 `@local/dsh-course-selflearn` 那一行并重启 DSH；
   或 `cp ~/.dsh/profiles/desktop/package.json.bak ~/.dsh/profiles/desktop/package.json` 后重启。
2. 想把 skill 找回来：把 `skills/course-selflearn/` 拷回 `<工作区>/.dsh/skills/course-selflearn/`。

**两条路互不依赖**——这正是「删掉插件退回原行为」可以真做实验的前提，也是第五节判据 4
（可回滚）在本节的具体形态。

### 6. 常见失败原因

| 现象 | 原因 |
| --- | --- |
| 装完 skill 没出现 | bundle 的 patch 裸写了 `- id:`（没被 `- insert:` 包住）→ boot 当成覆盖，warn + skip |
| 装完 skill 没出现，日志里也没报错 | 工作区的手工副本还在，近的层赢了（判据 2 就是为这个加的） |
| 启动失败 | patch 里的 `name` 与 `package.json` 的包名不一致；或 `index.js` 算出的 `SKILLS_DIR` 指错 |
| 技能目录里没有它，日志报名字非法 | `SKILL.md` 的 frontmatter `name` 不是 kebab-case |

排查顺序：`~/.dsh/logs/` 最新日志里搜 `course-selflearn` → 核对包名 → 核对 patch 顶层有 `- insert:`。

> 本仓库不带「插件副本 ↔ 真源」的同步闸门（那是分发者工作区的工具，要同时看到两棵树）。
> 所以对使用者来说，能验的就是本节的 5 条判据；对分发者来说，副本必须**逐字**，
> 拷文件用二进制方式（文本模式会把 LF 翻成 CRLF，结果 `git status` 说改了、`git diff` 是空）。
