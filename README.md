# dsh-course-selflearn —— 把 skill 装成插件

这个 bundle 只做一件事：让官方的文件系统 skill provider 去读本包自带的
`skills/course-selflearn/`（`SKILL.md` + 5 篇 `references/`），
于是每个会话的技能目录里都有它，不用手工拷到 `<工作区>/.dsh/skills/`。

**没有 index.js，不注册工具，不挂 hook，没有 schema 成本。** 它是一份纯配置：
`cordis.patch.yml` 里一行 insert，把 `@deepseek-ai/dsh-skill-filesystem` 挂到本包的
`skills/` 上。判据（为什么不做成 tool 型 / hook 型）写在工具链文档
`docs/05-插件化决策.md`。

## 为什么没有 index.js（实测结论，别改回去）

本包是用 `link:` 装进 profile 的。Node 解析裸模块名时按**真实路径**走，而链接包的
真实路径在工作区里——那里没有 `@deepseek-ai/*`。所以 index.js 里写

```js
import { FileSystemSkillProvider } from '@deepseek-ai/dsh-skill-filesystem'
```

会直接失败（实测：`ERR_MODULE_NOT_FOUND` → `1 entry did not activate`）。
把导入交给 **loader** 才行：它用 profile 的 `baseUrl` 解析内建包，这条路是通的
（同一工作区里另一个 link 装的 bundle 引用 `@deepseek-ai/dsh-mcp-client` 就是靠它）。

## 装

```
plugin_manager install_bundle target=<本目录绝对路径>
```

装完看两件事：

1. 返回里 `application` 是 `applied`（不是只有 `warnings`）；
2. 开一个新会话，技能目录里出现 `course-selflearn`，并且它报的资源基址是
   **本包内**的 `skills/course-selflearn/`（而不是工作区里那份手工副本）。

第 2 条是唯一能区分「谁供的 skill」的观测量。只看「skill 能用」会把两条路混为一谈：
手工副本一直在的话，插件没生效也照样能用。

## 卸

从 profile 的 bundles 列表移除 `@local/dsh-course-selflearn` 并重启 DSH（或
`plugin_manager list_bundles` 按返回的 nextStep 操作）。

**回滚到原行为**：卸掉插件后，把 `skills/course-selflearn/` 拷回
`<工作区>/.dsh/skills/course-selflearn/` 即可——默认根目录照旧扫得到它。两条路互不依赖。

## 真源与同步

`skills/course-selflearn/` 是工具链仓库里那份的**逐字副本**，不是改写版。
改了任何一边都要跑同步闸门（工作区根目录）：

```
python tools/check_plugin_sync.py
```

退出码 0 才算一致。为什么必须二进制拷（`shutil.copyfile`）：文本模式在 Windows 上
会把 LF 翻成 CRLF，结果 `git status` 说改了、`git diff` 是空。

## 运行时契约

| 依赖 | 谁提供 | 用在哪 |
| --- | --- | --- |
| `@deepseek-ai/dsh-skill-filesystem` | DSH 自身（不是本包依赖） | patch 里的 `name:`，由 loader 解析 |
| `ctx.skills` 注册表 | `dsh-base` 先挂好 | provider 注册的目标 |

`customSkillDirs` 的值由 `!!js` 当场算出：用 profile 目录的 `createRequire` 解析本包
`package.json`，取目录再拼 `skills`。所以配置里不出现任何机器路径。

## 显示资源

本 bundle 没有 client 半边，也不渲染界面，所以不带 `icon.svg` 与 `locale/`。
同类已装插件（`dsh-logicprobe`、`dsh-embedded-workbench`）同样不带图标；
可用的是 `dsh.displayName` + `dsh.category: skill` 这两项。
