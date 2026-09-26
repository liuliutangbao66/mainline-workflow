# 安装与首次使用

## 环境要求

- 能读取本地Skill、文件和运行命令的Codex环境。普通网页聊天没有本地文件访问时，不能仅粘贴Skill名称就使用此套件。
- 主线工具要求Python 3.10或更高；没有第三方运行依赖，不需要额外API Key。实际验证环境为Windows/Python 3.14.7；其他系统和最低版本尚未逐一实测。
- 先下载或取得本仓库，进入包含README.md、skills/和examples/的根目录。两个技能目录均包含SKILL.md；不要只下载单个Markdown文件。

先运行 `python --version`。如果Windows使用 `py` 启动Python，将文中各处 `python` 替换为 `py -3`；这条替代启动方式未在本轮另行验证。

## 选择安装目录

截至2026-09-27，官方文档列出的个人目录是 `~/.agents/skills`，项目级目录是项目内的 `.agents/skills`。我们此前本机安装在 `.codex/skills` 并已识别；不要把该历史本机路径当成所有环境的唯一位置。以你使用的宿主当前文档/配置为准，已有可识别安装不必迁移。参见 [OpenAI官方技能文档](https://learn.chatgpt.com/docs/build-skills)。

同名Skill出现在多个扫描目录可能造成重复入口，因此先检查是否已有 task-card-refiner 或 mainline。存在时先备份并核对版本，本安装步骤不会覆盖。升级技能目录也不会更新既有项目中的固定 `.workflow` 副本。

## Windows：复制两个完整目录

在仓库根目录的PowerShell运行以下整段。默认使用官方个人目录；如需使用已确认的其他目录，可先设置 `MAINLINE_SKILL_ROOT` 为那个完整路径。此变量仅用于下面的复制步骤，不会改变Codex的扫描配置。

```powershell
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath '.\skills').Path
$targetRoot = if ($env:MAINLINE_SKILL_ROOT) { $env:MAINLINE_SKILL_ROOT } else { Join-Path $env:USERPROFILE '.agents\skills' }
$names = @('task-card-refiner', 'mainline')
foreach ($name in $names) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot "$name\SKILL.md"))) { throw "源文件不完整：$name" }
    if (Test-Path -LiteralPath (Join-Path $targetRoot $name)) { throw "目标已存在，先核对并备份：$name" }
    if (-not $env:MAINLINE_SKILL_ROOT) {
        $legacyRoot = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $env:USERPROFILE '.codex\skills' }
        if (Test-Path -LiteralPath (Join-Path $legacyRoot $name)) { throw "发现既有安装，请先确认使用哪个目录：$name" }
    }
}
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
foreach ($name in $names) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $targetRoot $name) -Recurse
}
Get-ChildItem -LiteralPath $targetRoot -Directory | Where-Object Name -In $names | Select-Object Name
```

预计得到以下结构：

```text
个人技能目录/
  task-card-refiner/
    SKILL.md
    agents/openai.yaml
    references/mainline-entry.md
  mainline/
    SKILL.md
    agents/openai.yaml
    assets/COMMANDS.md
    assets/PROTOCOL-0.1.0.md
    scripts/flow.py
```

复制操作没有全盘事务：若磁盘或权限错误造成中途失败，先核对已复制的目录，保留旧版本，不盲目覆盖重试。

macOS/Linux可以把这两个完整文件夹手工复制到宿主支持的个人技能目录，并检查上述结构。本轮没有在这些系统执行安装，因此不声称跨系统安装已经验证。

## 如何确认成功

1. **文件层面**：两个目录结构完整；使用安装位置中的 `mainline/scripts/flow.py --help` 能显示帮助。这只验证文件与Python工具。
2. **宿主层面**：在技能选择器中找到“任务卡整理”和“主线管家”，或以 `$task-card-refiner`、`$mainline` 选择。官方说明支持自动发现变更；如果没有出现，重启Codex后再检查。不要为了排障在多个目录重复安装。[官方说明](https://learn.chatgpt.com/docs/build-skills)
3. **运行层面**：从仓库根目录运行下述案例。若要验证刚复制的安装，加 `--skills-dir`，其值为上面的个人技能目录（包含两个技能子目录的那一级）。

```text
python -B -X utf8 examples/duration-summary/replay.py --output demo-runs/install-check
```

成功时第二轮结果为有效3行、平均20毫秒，无效ID为r3和r4；输入及第一轮文件保持不变。命令日志和逐项审查记录位于新建的输出目录。输出目录已经存在时选择新路径，不删除旧证据。

## 第一次真正使用

选择目标项目或准备好它的明确目录。在整理会话里只输入：

```text
$task-card-refiner
我想……
背景资料：……
约束和合格标准：……
```

这些是示意，不是必填表格。已有材料直接贴入即可。它会询问关键缺口，最后输出完整启动卡；默认只整理，不实施。

把启动卡完整复制给主线会话。它核对项目目录、规则及实际授权后继续；若卡片仅授权核查/规划，业务实施仍需你的明确要求。执行者完成后可通知主线“本阶段已交付，请审查”，无需复制整份报告。

首次初始化会创建项目内 `.workflow/`；没有AGENTS.md时另建入口，已有AGENTS.md则保留。之后的任务、报告和证据按需创建。仅安装Skill不会初始化任何项目。

## 换会话与停用

换主线会话时按项目的交接入口读取当前状态，不把整个历史目录全部加载。明确转交主线后旧主线停止写入。

如需停用，将个人目录中这两个Skill目录移动到不会被扫描的备份位置即可；先确认目录归属。不要删除目标项目的 `.workflow`，它保存当前状态和证据，且已初始化项目使用自己的固定工具。旧项目仍可按其入口继续。
