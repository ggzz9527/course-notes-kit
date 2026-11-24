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
