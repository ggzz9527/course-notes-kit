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
python -c "import io,yaml;t=io.open('$HOME/.dsh/profiles/desktop/cordis.patch.yml',encoding='utf-8').read().replace('!!js','');d=yaml.safe_load(t);print('OK',len(d));print([x.get('id') for x in d])"
```

**判据**：能解析，且 id 列表末尾出现你的新条目。

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

## 五、「插件」的口径与交付判据

**本包把「插件」定义为 (b) agent preset**，不是 cordis 插件。理由只有一条，但它是硬的：
cordis 插件要把工具注册成命令，等于把每个工具的 schema 塞回**每一条**会话；
而这个 preset 存在的全部意义就是把首轮固定开销从 ≈70,700 token 压到 ≈3,633。
选 (a) 会正面撞上它自己的设计目标。

所以交付判据就是下面四条，缺一条不算装好：

| # | 判据 | 怎么验 |
| --- | --- | --- |
| 1 装上了 | 「模式」下拉有「笔记整理」；首轮工具数 ≈20（不是 175） | 新会话问「列一下你有哪些工具」 |
| 2 自证 | 脚本链在这台机器上真能跑 | `python scripts/selfcheck.py` → 7/1/0；`python scripts/regress_frames.py` → 6/0；`python scripts/regress_download.py` → 6/0；三个对账 + `scrub_kit` 全 exit 0 |
| 3 零运行逻辑 | preset 里没有运行逻辑、不注册新命令、工具白名单固定 | 数 `agent.cordis.yml` 里的 `- id:` 行；每加一个工具就重算 token |
| 4 可回滚 | 删掉 patch 里那一段就卸载 | 见上面「三、失败怎么回滚」 |

第 2 条为什么要单列：**preset 装对了不等于流程能跑**。工具白名单对、但脚本缺依赖
（ffmpeg / faster-whisper）时会话看起来正常，一 `fetch` 就炸——那时候不容易判断是
preset 的问题还是环境的问题。先跑 `selfcheck`，把「这台机器能不能用」和
「preset 装没装对」分开验。
