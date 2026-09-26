# 验证范围

2026-09-27，Windows / Python 3.14.7。运行工具及协议为0.1.0；发布候选包含最新的两个Skill说明。本页记录维护者实际执行的检查，不代表未列出的环境与行为已经验证。

## 已执行

| 检查 | 实际结果 | 证明范围 |
|---|---|---|
| 从本候选运行工具回归 | 36项，35通过、1跳过 | 生命周期、冻结对象、返工、状态、交接、路径等确定性规则 |
| CSV案例完整复现 | 首轮5条/12毫秒被退回；第二轮3条/20毫秒被接受 | 真实程序与工具执行；角色和访谈仍是教学设定 |
| 交接与证据保留 | guide-a转guide-b；第一轮文件与原始CSV保持不变 | 交接记录及命令行为，不是真实新会话理解能力 |
| 安装文档中的PowerShell整段代码 | 在隔离目标执行，8文件与源相同 | 文件复制；不代表隔离路径已被Codex自动发现 |
| 已有安装与已有案例输出 | 重复操作拒绝，文件未改 | 防止意外覆盖 |
| 从安装副本运行帮助与完整案例 | 通过，安装文件未变化 | 复制后的工具可执行 |

跳过项：Windows本轮未授予创建符号链接的能力，相关测试没有实际执行，不能将其记为通过。

## 如何复查

在仓库根目录：

```text
python -B -X utf8 -m unittest discover -s tests -v
python -B -X utf8 examples/duration-summary/replay.py --output demo-runs/verification
```

实际案例汇总见 [observed-result.json](../examples/duration-summary/observed-result.json)，命令返回记录见 [observed-commands.json](../examples/duration-summary/observed-commands.json)。这些是发布候选准备时的运行快照；重新执行会产生新的时间戳和候选哈希，不应要求哈希与历史运行相同。

## 尚未验证

- 两个Skill在独立Agent中的完整多轮采访、收敛质量及真实新会话接手。
- Linux/macOS、Python最低声明版本3.10、远程挂载与跨机器协作。
- 全部CSV异常情况和通用统计工具质量；案例仅用于展示工作流。
- 长期使用负担、自动上下文容量控制、自动调度与后台通知。后几项并非本版已有能力。

check只核对文件和记录一致性；角色标签不认证实际身份。接受特定候选不等于部署、独立验证或用户验收。
