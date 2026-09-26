"""Offline scripted demonstration. Does not invoke models or create agent chats."""
import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

# The first version intentionally counts invalid rows as zero to demonstrate rework.
PROGRAM = '''import csv, json, math, sys
values, rejected = [], []
with open(sys.argv[1], encoding="utf-8", newline="") as source:
    for row in csv.DictReader(source):
        try:
            value = float(row["duration_ms"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("invalid duration")
        except ValueError:
            rejected.append(row["id"])
            INVALID_ACTION
        values.append(value)
result = {"valid_count": len(values), "mean_ms": sum(values) / len(values) if values else None,
          "invalid_ids": rejected}
print(json.dumps(result, ensure_ascii=False))
'''


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New directory; existing paths are refused')
    parser.add_argument('--skills-dir', type=Path, default=REPO / 'skills', help='Optional installed skill directory')
    args = parser.parse_args()
    root = Path(args.output).absolute()
    if root.exists() or root.is_symlink():
        parser.error('Output already exists; choose a new directory. Nothing was overwritten.')
    installed_tool = args.skills_dir.resolve() / 'mainline/scripts/flow.py'
    if not installed_tool.is_file():
        parser.error('mainline/scripts/flow.py is missing from the selected skills directory')
    root.mkdir(parents=True)
    (root / 'inputs').mkdir()
    shutil.copyfile(HERE / 'input.csv', root / 'inputs/durations.csv')
    input_hash = digest(root / 'inputs/durations.csv')
    tool = installed_tool
    owner = 'guide-a'
    events = []

    def command(cmd, data=None, to=None):
        argv = [sys.executable, '-B', '-X', 'utf8', str(tool), '--root', str(root), cmd]
        if cmd not in ('status', 'brief', 'check'):
            argv += ['--actor', owner]
            if cmd != 'init':
                state = json.loads((root / '.workflow/state.json').read_text(encoding='utf-8'))
                argv += ['--expect', str(state['revision'])]
            if data is not None:
                relative = f'demo-log/request-{len(events):02d}-{cmd}.json'
                dump(root / relative, data)
                argv += ['--data', str(root / relative)]
            if to:
                argv += ['--to', to]
        proc = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8')
        # Store portable command names and actual JSON output, not local absolute paths.
        output = json.loads(proc.stdout) if proc.stdout else {'stderr': proc.stderr}
        events.append({'command': cmd, 'actor': owner, 'exit_code': proc.returncode, 'result': output})
        dump(root / 'demo-log/commands.json', events)
        if proc.returncode:
            raise RuntimeError(f'{cmd}: {output}')
        return output

    command('init', {'name': 'CSV 耗时统计示例', 'goal': '离线汇总有效耗时并记录无效行，不改原始CSV',
                     'acceptance': ['A1：有效行数和平均值可复算', 'A2：无效行ID完整列出', 'A3：输入哈希不变'],
                     'constraints': ['只在示例目录读写，不联网、不付费、不发布', '空值、非数字、非有限数和负数不计入平均值']})
    tool = root / '.workflow/runtime/flow.py'
    shutil.copyfile(HERE / 'startup-card.md', root / '.workflow/STARTUP.md')
    command('task', {'title': '实现并验证CSV耗时汇总', 'goal': '交付可复算的离线统计脚本与结果',
                     'authorization': '教学夹具允许本地执行与角色标签交接；不是实际跨会话委派',
                     'executor': 'demo-worker', 'scope': ['outputs'],
                     'inputs': ['inputs/durations.csv', '.workflow/STARTUP.md'], 'independent': False,
                     'acceptance': [
                         {'id': 'A1', 'criterion': '有效3行，平均20毫秒', 'method': '读取CSV，用Decimal独立复算并比较输出'},
                         {'id': 'A2', 'criterion': '无效行仅r3和r4', 'method': '核对无效行ID集合'},
                         {'id': 'A3', 'criterion': '原始CSV字节不变', 'method': '比较执行前后的SHA256'}],
                     'stop_conditions': ['输入列缺失或不可读时停止', '发现输入被改动则不得验收']})
    command('start')

    # Reviewer calculation uses Decimal and a separate pass over the source data.
    valid, invalid = [], []
    with (root / 'inputs/durations.csv').open(encoding='utf-8', newline='') as source:
        for row in csv.DictReader(source):
            try:
                value = Decimal(row['duration_ms'])
                if not value.is_finite() or value < 0:
                    raise InvalidOperation
            except InvalidOperation:
                invalid.append(row['id'])
            else:
                valid.append(value)
    expected = {'valid_count': len(valid), 'mean_ms': float(sum(valid) / len(valid)), 'invalid_ids': invalid}
    if expected != {'valid_count': 3, 'mean_ms': 20.0, 'invalid_ids': ['r3', 'r4']}:
        raise RuntimeError('Fixture changed; review the demonstration expectations')

    def deliver(attempt, buggy):
        base = f'.workflow/tasks/T001/attempt-{attempt:02d}'
        output_dir = root / base / 'outputs'
        output_dir.mkdir()
        source_path = output_dir / 'summarize.py'
        source_path.write_text(PROGRAM.replace('INVALID_ACTION', 'value = 0.0' if buggy else 'continue'), encoding='utf-8')
        proc = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(source_path), str(root / 'inputs/durations.csv')],
                              capture_output=True, text=True, encoding='utf-8', check=True)
        observed = json.loads(proc.stdout)
        dump(output_dir / 'summary.json', observed)
        dump(root / base / 'execution.json', {'exit_code': proc.returncode, 'stdout': observed})
        (root / base / 'report.md').write_text(
            '# 执行报告\n脚本执行结束，已产出summary.json；尚未经过主线验收。\n'
            '现场：无后台进程。限制：仅此教学输入，不证明任意CSV兼容性。\n', encoding='utf-8')
        command('submit', {'artifacts': [f'{base}/outputs'], 'evidence': [f'{base}/execution.json'],
                           'claim': '脚本可运行并输出统计结果；正确性待审查', 'limits': '单一教学输入',
                           'next_action': '复算并核对各验收项'})
        return base, observed

    first_base, first = deliver(1, True)
    first_brief = command('brief')
    if first != {'valid_count': 5, 'mean_ms': 12.0, 'invalid_ids': ['r3', 'r4']}:
        raise RuntimeError('Deliberately flawed demonstration did not reproduce expected failure')
    command('handoff', {'reason': '演示待审查阶段的指导者标签交接', 'next_action': '读取冻结交付，复算后决定验收或返工',
                        'completed': '第一轮脚本运行并提交', 'remaining': '主线核对统计口径',
                        'processes': '无后台进程，子进程已退出', 'budget': '无付费或实局预算',
                        'worktree': '本示例未使用Git', 'ruled_out': '不是输入不可读，已产生输出',
                        'stop_conditions': '统计口径不符则返工；输入变化不得验收'}, to='guide-b')
    owner = 'guide-b'
    resumed = command('brief')
    dump(root / 'demo-log/resumed-brief.json', resumed)
    first_evidence = f'{first_base}/review-evidence.json'
    dump(root / first_evidence, {'expected': expected, 'observed': first, 'A1_pass': False,
                                 'finding': '无效行被当作0计入分母，使均值从20变成12'})
    command('review', {'decision': 'rework', 'candidate_id': resumed['candidate_id'], 'reviewer': owner,
                       'findings': 'A1失败：5行/12毫秒，应为3行/20毫秒', 'limits': '脚本化教学审查，无独立Agent',
                       'next_action': '在第二轮跳过无效行，保留第一轮文件', 'evidence': [first_evidence]})
    first_files = {p.relative_to(root).as_posix(): digest(p) for p in (root / first_base).rglob('*') if p.is_file()}
    command('start')
    final_base, final = deliver(2, False)
    state = command('status')
    check_evidence = f'{final_base}/review-evidence.json'
    checks = {'A1': final['valid_count'] == expected['valid_count'] and math.isclose(final['mean_ms'], expected['mean_ms']),
              'A2': final['invalid_ids'] == expected['invalid_ids'], 'A3': digest(root / 'inputs/durations.csv') == input_hash}
    dump(root / check_evidence, {'expected': expected, 'observed': final, 'checks': checks, 'input_sha256': input_hash})
    if not all(checks.values()):
        raise RuntimeError('Second round failed verification; refusing acceptance')
    command('review', {'decision': 'accept', 'candidate_id': state['candidate_id'], 'reviewer': owner,
                       'findings': 'A1-A3逐项核对通过，接受第二轮冻结交付', 'limits': '仅示例输入，不代表用户或独立Agent验收',
                       'next_action': '交用户查看演示结果', 'evidence': [check_evidence],
                       'checks': {key: {'result': 'pass', 'finding': '见复算与输入哈希记录', 'evidence': [check_evidence]} for key in checks}})
    command('note', {'summary': 'T001第二轮已接受：有效3行，均值20毫秒，无效r3/r4；首轮错误与返工记录保留。',
                     'next_action': '演示结束；不自动开展其他项目任务。'})
    integrity = command('check')
    final_brief = command('brief')
    if final_brief['active_task'] is not None or final_brief['last_accepted'] != 'T001':
        raise RuntimeError('Final state is unexpected')
    if any(digest(root / p) != checksum for p, checksum in first_files.items()):
        raise RuntimeError('First round evidence was modified')
    result = {'first_round': first, 'second_round': final, 'input_unchanged': checks['A3'],
              'first_round_files_preserved': True, 'handoff_owner': final_brief['owner'],
              'first_submit_status': first_brief['task_status'], 'final_revision': final_brief['revision'],
              'last_accepted': final_brief['last_accepted'], 'integrity': integrity,
              'evidence_boundary': 'Scripted role labels and teaching dialogue; no model calls or independent agents.'}
    dump(root / 'demo-result.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
