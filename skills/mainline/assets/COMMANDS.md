# Agent操作说明

Python 3.10+，仅标准库。以下JSON由Agent根据真实需求准备，用户无需填写。输入文件可放临时目录，不是第二份状态源。命令只做登记，不运行JSON里的业务指令。

首次使用原套件 `skills/mainline/scripts/flow.py`；之后使用项目内固定工具 `.workflow/runtime/flow.py`。所有命令先 `--root <项目目录>`，再子命令。写入须 `--actor <当前主线ID> --expect <status返回的revision>`；init不需要expect。每次成功写入修订号加一，不硬编码连续猜测。

## 初始化

`python <套件>/skills/mainline/scripts/flow.py --root <目录> init --actor guide-01 --data <项目JSON>`

```json
{"name":"研究报告","goal":"交付有来源可核查的研究报告","acceptance":["结论对应来源，未知项明确"],"constraints":["只使用公开材料，不对外发布"]}
```

目录需已存在；不覆盖.workflow。已有AGENTS保持原样，工具返回需读取的入口。所有相对路径使用正斜杠，允许中文和空格。

## 只读

`status`：当前任务、修订、候选ID、下一步；`brief`：再加最小接手入口；`check`：检查固定文件、证据、视图与当前候选。JSON输出，成功exit 0，拒绝或检查错误exit 1。历史候选变化作为warning，不撤销历史接受，不可据此声称当前版本已通过。

status/brief同时披露当前候选与最近接受对象是否仍匹配文件。旧轮证据漂移在check中保留warning，不永久阻塞已经重新验证的新轮；冻结的提交/审查记录自身被改动仍视为错误。已完成任务的中途交接记录留在history，不再加入最小必读集合。

## 创建及启动任务

`task --actor guide-01 --expect 0 --data <任务JSON>`

```json
{
  "title":"形成第一版报告","goal":"完成一份可审查报告",
  "scope":["outputs"],"inputs":[],"authorization":"允许本地写报告，不发布、不付费",
  "executor":"worker-01","independent":false,
  "acceptance":[{"id":"A1","criterion":"每个主要结论有来源","method":"逐项核对原文"}],
  "stop_conditions":["来源不可核查时标未知，不编造"]
}
```

`start --actor guide-01 --expect 1`。执行者在任务范围内工作。当前轮输出位置由status返回，报告为其中report.md；允许把阶段成果放该轮outputs子目录。scope只约束申报候选路径，不能拦截所有实际写入。

## 提交

主线先读交付，再 `submit --actor guide-01 --expect <修订> --data <提交JSON>`。

```json
{"artifacts":["outputs"],"evidence":[".workflow/tasks/T001/attempt-01/source-check.md"],"claim":"已形成初稿并核对来源","limits":"未经过用户主观验收","next_action":"指导者审查主要结论与原文"}
```

report.md必须已存在且非空。artifacts可选择文件或目录；目录内容变化也被检测。证据选择具体文件，避免把不断变化的状态、整个业务目录或凭据纳入。哈希记录不是成果备份。

ready状态却已发现报告时，先核查归属、对象及停止状态，在提交JSON增加existing_delivery_checked字段写明核查依据，再submit。这样无需伪造start或重跑业务。blocked状态先依据真实解除条件resume。

## 审查与返工

`review --actor guide-01 --expect <修订> --data <审查JSON>`

```json
{
  "decision":"accept","candidate_id":"使用status的candidate_id",
  "reviewer":"guide-01","findings":"已逐项核对主要结论与来源",
  "limits":"仅接受当前文件版本，不代表发布或用户验收","next_action":"交用户确认表达与用途",
  "evidence":[],
  "checks":{"A1":{"result":"pass","finding":"主要结论均可回溯原文","evidence":[".workflow/tasks/T001/attempt-01/source-check.md"]}}
}
```

accept必须覆盖每项验收并引用冻结证据。需独立验证时reviewer必须为真实不同的验证者，并提交自己的证据；主线仍以actor登记最终决定。独立证据中的候选标识应与candidate_id一致，不能把旧报告用于新版本。

返工使用decision=rework，写明findings、limits、next_action、reviewer、candidate_id和evidence。可缺checks；允许当前候选或证据已变化时退回，但保留旧冻结记录。自动分配新轮ready，旧轮不可覆写。范围改变用cancel后新任务。

## 维护与交接

- `note --data ...`：`{"summary":"当前有依据的判断","next_action":"下一条件或动作"}`。替换当前简述，留必要旧判断。
- `block --data ...`：`{"reason":"确认证据缺口","next_action":"补充所缺材料"}`；`resume`同形输入，reason说明解除依据。
- `cancel --data ...`：同形输入，停止当前契约，保留历史。不终止实际进程，须另外核实停止。
- `handoff --to guide-02 --data ...`：基本字段reason、next_action。活动任务已开始时还须completed、remaining、processes、budget、worktree、ruled_out、stop_conditions，均为真实文本或明确“未知”。仅在用户授权交接后登记。
- `sync`：不需要data。STATE被手改或中断后失步时，先保存已有视图到history，再从权威状态重建；不会把手写视图自动当作新事实。

## 中断与恢复

写入锁只防同机命令同时写；残留锁需确认无其他操作者和相关进程后再由有权限的指导者处理。工具不会自动删锁。状态先原子提交，再刷新阅读视图；若仅视图未生成，用sync恢复。任务/提交记录先独占写入再登记；若意外中断留下未登记文件，保留它们并核对，不盲目重跑或覆盖。本版未提供自动事务回滚。

项目目标和约束目前是冻结入口；需要改变时先形成可审阅的新契约和迁移方案，不能手改绕过哈希检查。常规阶段变化通过新任务表达。
