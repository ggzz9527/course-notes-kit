# 装成插件 / 装到别的 agent

skill 的正文只有一份：`skills/course-selflearn/`（`SKILL.md` + 6 篇 `references/`）。
其它形式都是它的**包装**，不是改写版：

| 目标 | 用什么 | 产物 |
| --- | --- | --- |
| DSH | `plugin/`（纯配置 bundle） | 每个会话的技能目录里都有 `course-selflearn` |
| 技能面板式 agent / Claude Code / 通用 | `scripts/make_agent_package.py` | `dist/<target>/course-selflearn.zip` 或目录 |

## 一、DSH：装成插件

```bash
git clone https://github.com/ggzz9527/course-notes-kit.git
# 然后在 DSH 会话里（把 <clone> 换成实际路径）：
plugin_manager install_bundle target=<clone>/plugin
```

装完看两件事：

1. 返回里 `application` 是 `applied`（不是只有 `warnings`）；
2. **开新会话**，技能目录里出现 `course-selflearn`，且它报的资源基址是 `plugin/skills/course-selflearn/`
   ——这是唯一能区分「谁供的 skill」的观测量；只看「skill 能用」会把插件和手工副本混为一谈。

卸：从 profile 的 bundles 列表移除 `@local/dsh-course-selflearn` 并重启 DSH。

`plugin/` 是**纯配置**：没有 `index.js`、不注册工具、不挂 hook。为什么不能有 `index.js`
（`link:` 装的包按真实路径解析，`import '@deepseek-ai/*'` 会 `ERR_MODULE_NOT_FOUND`）、
为什么不做成 tool 型 / hook 型，判据在 `docs/05-插件化决策.md`。

## 二、技能面板式 agent（上传 SKILL.md 技能包）

带「技能面板 + 上传技能包」的桌面 agent 用这一类（技能格式都是 YAML 前言 + Markdown 正文）：

```bash
python scripts/make_agent_package.py skill-panel
# 产物：dist/skill-panel/course-selflearn.zip  和  dist/skill-panel/course-selflearn/
```

两条装法（任选）：

1. **上传**：技能面板 → 添加技能 → 上传技能包 → 选 `course-selflearn.zip`；
2. **本地**：把 `course-selflearn/` 整个目录拷到该产品的技能目录（多数是 `~/.<产品名>/skills/`），
   然后**完全重启**它。

那一份 `SKILL.md` 与真源的差别只有 frontmatter：去掉 DSH 专有的 `whenToUse`，补 `version`/`tags`。
正文逐字相同。

**技能只是「怎么做」**：跑管线仍要那台机器上有本仓库的 `tools/` 和 Python 环境，
所以在那台机器上先把工作目录指到 clone 下来的仓库根，再说「用 course-selflearn 整理这门课」。

## 三、Claude Code / 通用 agent

```bash
python scripts/make_agent_package.py claude     # 拷到 ~/.claude/skills/ 或 <项目>/.claude/skills/
python scripts/make_agent_package.py generic    # SKILL.md 当系统提示，或放进它的技能目录
```

`references/*.md` 是正文引用的补充规范（模板、复查清单、工具映射），让 agent 能按路径读到即可。

## 四、换 agent 时判据不变

正文里出现的 `workflow` / `read_image` / `ops_bash` 是 DSH 的工具名。其它 agent 只需换调用名——
对应表和降级方式在 `skills/course-selflearn/references/其它-agent-工具映射.md`。

**四样东西不随 agent 变**：`tools/` 的脚本与退出码、`references/` 的写作规范、
集目录约定、媒体目录布局。所以「换 agent」不需要重验整个流程，只验三条：

```bash
python scripts/selfcheck.py                       # 7 通过 / 1 警告 / 0 失败
python scripts/regress_frames.py                  # 6 通过 / 0 失败
python tools/course_gate.py ruler <集目录> --strict
```

## 五、改了一边要跑同步闸门

`plugin/skills/` 是 `skills/` 的逐字副本。改了任何一边：

```bash
python scripts/check_skill_sync.py     # 判据：退出码 0；打印「逐字一致 N 个文件」
```

它同时查 bundle 自洽（`- insert:` 包裹、provider 名、`includeDefaultRoots: false`、
`customSkillDirs` 的 `!!js` 解析的是本包），因为「内容一样但装不上」是另一类事故。
