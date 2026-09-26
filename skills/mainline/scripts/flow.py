#!/usr/bin/env python3
"""Project-local workflow records. No model calls, process launch, or permissions sandbox."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sys
import tempfile

VERSION = "0.1.0"
META = ".workflow"
STATUSES = {"ready", "running", "pending_review", "blocked", "accepted", "cancelled"}


class FlowError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise FlowError(message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label}: 必须填写非空文本")
    return value.strip()


def strings(value, label, required=False):
    require(isinstance(value, list), f"{label}: 必须是列表")
    if required:
        require(bool(value), f"{label}: 不能为空")
    for item in value:
        text(item, label)
    require(len(value) == len(set(value)), f"{label}: 不应重复")
    return value


def read_json(path):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise FlowError(f"无法读取JSON {path}: {exc}") from exc
    require(isinstance(result, dict), f"{path}: 必须为JSON对象")
    return result


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def linked(path):
    return path.is_symlink() or (path.exists() and bool(
        getattr(path.lstat(), "st_file_attributes", 0) & 0x400))


def relative(value):
    text(value, "路径")
    require("\\" not in value and ":" not in value, "使用项目内相对路径及正斜杠")
    p = PurePosixPath(value)
    require(not p.is_absolute() and not PureWindowsPath(value).is_absolute(), "不接受绝对路径")
    require(value != "." and all(v not in (".", "..") for v in value.split("/")), "路径不能含 . 或 ..")
    require(all(v and v == v.rstrip(" .") for v in value.split("/")), "路径含空段或歧义名称")
    return p.as_posix()


def safe(root, value, exists=False):
    value = relative(value)
    p = root
    for part in PurePosixPath(value).parts:
        p = p / part
        require(not linked(p), f"不接受符号链接或junction: {value}")
    require(p.resolve().is_relative_to(root.resolve()), f"路径超出项目: {value}")
    if exists:
        require(p.exists(), f"文件不存在: {value}")
    return p


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".flow-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def encode(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def new_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as out:
        out.write(encode(data))


def save(root, state):
    state["updated_at"] = now()
    # Canonical state is committed first. A crash before rendering is repaired by sync.
    atomic(safe(root, f"{META}/state.json"), encode(state))
    atomic(safe(root, f"{META}/STATE.md"), render(state))


def load(root):
    state = read_json(safe(root, f"{META}/state.json", True))
    require(state.get("schema") == 1 and state.get("version") == VERSION, "未知流程版本；使用该项目固定的工具")
    require(isinstance(state.get("tasks"), dict), "状态缺少任务索引")
    for tid, task in state["tasks"].items():
        require(re.fullmatch(r"T\d{3,}", tid) is not None, "任务索引ID无效")
        require(task.get("status") in STATUSES, "任务状态无效")
        require(isinstance(task.get("attempt"), int) and task["attempt"] > 0, "任务轮次无效")
    require(state.get("active_task") is None or state["active_task"] in state["tasks"], "活动任务不存在")
    return state


def render(state):
    tid = state["active_task"]
    task = state["tasks"].get(tid) if tid else None
    lines = [f"# {state['name']} 当前状态", "", "工具生成视图；请通过 flow.py 更新，勿手工双写。",
             f"- 流程版本：{state['version']}；修订：{state['revision']}",
             f"- 主线维护者：{state['owner']}", f"- 更新时间：{state['updated_at']}",
             "", "## 目标", state["goal"], "", "## 当前判断", state["summary"], "", "## 当前任务"]
    if task:
        lines += [f"- {tid}：{task['status']}；第 {task['attempt']} 轮",
                  f"- 必读：[任务契约](tasks/{tid}/task.md)",
                  f"- 执行输出：tasks/{tid}/attempt-{task['attempt']:02d}/",
                  f"- 阻塞：{task.get('blocker') or '无已登记阻塞'}"]
    else:
        lines += ["无活动任务；不要重复派发历史任务。"]
    if state.get("last_accepted"):
        lines += ["", "## 最近接受", f"{state['last_accepted']}。结论只覆盖其冻结文件；使用 check 核对当前文件是否仍匹配。"]
    lines += ["", "## 下一步", state["next_action"], "", "## 接手入口",
              "先读本文件及 [项目约束](PROJECT.md)，再读当前任务；历史按需读取。",
              f"绑定协议：[v{VERSION}](protocol/{VERSION}.md)。",
              "命令：python .workflow/runtime/flow.py --root . status / check / brief",
              "进程、工作树、已消费预算须在实际环境核对；登记值不代表实时监控。", ""]
    if state.get("handoff") and state.get("handoff_task") == state["active_task"]:
        lines += [f"最近交接：{state['handoff']}（现场信息按需核实）。", ""]
    return "\n".join(lines)


def immutable_errors(root, hashes):
    errors = []
    for path, digest in hashes.items():
        try:
            p = safe(root, path, True)
            require(p.is_file() and sha(p) == digest, f"冻结文件已变化: {path}")
        except (FlowError, OSError) as exc:
            errors.append(str(exc))
    return errors


def view_matches(root, state):
    p = safe(root, f"{META}/STATE.md")
    return p.is_file() and p.read_text(encoding="utf-8") == render(state)


@contextmanager
def edit(root, actor, expected, allow_stale=False):
    lock = safe(root, f"{META}/write.lock")
    require(lock.parent.is_dir(), "项目未初始化")
    try:
        with lock.open("x", encoding="utf-8") as f:
            f.write(encode({"pid": os.getpid(), "actor": actor, "time": now()}))
    except FileExistsError as exc:
        raise FlowError("检测到写入锁；先核对其他操作者和进程，禁止自动抢锁") from exc
    try:
        state = load(root)
        require(state["owner"] == actor, "只有登记的主线维护者可改变生命周期")
        require(state["revision"] == expected, f"修订已改变：当前 {state['revision']}，请重新读取")
        require(sha(__file__) == state["tool_sha256"], "工具与项目固定版本不符")
        errors = immutable_errors(root, state["runtime_hashes"])
        require(not errors, "; ".join(errors))
        require(allow_stale or view_matches(root, state), "STATE.md失步或被手改；先运行sync保留副本并重建")
        yield state
        state["revision"] += 1
        save(root, state)
    finally:
        lock.unlink()


def init(root, data, actor):
    require(root.is_dir(), "目标项目目录必须已存在")
    require(not linked(root), "目标根目录不能是链接")
    meta = safe(root, META)
    require(not meta.exists(), ".workflow已存在；拒绝覆盖，请接手已有项目")
    name, goal = text(data.get("name"), "name"), text(data.get("goal"), "goal")
    criteria = strings(data.get("acceptance"), "acceptance", True)
    constraints = strings(data.get("constraints"), "constraints", True)
    text(actor, "owner")
    assets = Path(__file__).resolve().parent.parent / "assets"
    protocol = assets / f"PROTOCOL-{VERSION}.md"
    require(protocol.is_file(), "初始化必须使用原始套件；项目内固定工具只用于后续管理")
    state = {"schema": 1, "version": VERSION, "name": name, "goal": goal,
             "revision": 0, "owner": actor, "active_task": None, "last_accepted": None,
             "summary": "项目已初始化；尚无阶段交付。", "next_action": "根据目标准备第一个必要阶段；简单工作可直接完成。",
             "tasks": {}, "tool_sha256": sha(__file__), "runtime_hashes": {}}
    # Stage installation; rename only when all runtime files are complete.
    staging = Path(tempfile.mkdtemp(prefix=".workflow-init-", dir=root))
    (staging / "runtime").mkdir()
    (staging / "protocol").mkdir()
    shutil.copyfile(__file__, staging / "runtime/flow.py")
    shutil.copyfile(protocol, staging / f"protocol/{VERSION}.md")
    entry = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(encoding="utf-8")
    (staging / "ENTRY.md").write_text(entry.replace(
        "](assets/COMMANDS.md)", "](COMMANDS.md)"), encoding="utf-8")
    shutil.copyfile(assets / "COMMANDS.md", staging / "COMMANDS.md")
    (staging / "PROJECT.md").write_text(
        f"# {name}\n\n## 目标\n{goal}\n\n## 项目验收\n" + "\n".join(f"- {v}" for v in criteria)
        + "\n\n## 授权与约束\n" + "\n".join(f"- {v}" for v in constraints) + "\n", encoding="utf-8")
    for p in ("runtime/flow.py", f"protocol/{VERSION}.md", "ENTRY.md", "COMMANDS.md", "PROJECT.md"):
        state["runtime_hashes"][f"{META}/{p}"] = sha(staging / p)
    state["updated_at"] = now()
    (staging / "state.json").write_text(encode(state), encoding="utf-8")
    (staging / "STATE.md").write_text(render(state), encoding="utf-8", newline="\n")
    staging.rename(meta)
    agents = root / "AGENTS.md"
    if not agents.exists() and not linked(agents):
        with agents.open("x", encoding="utf-8") as out:
            out.write("# 项目入口\n\n跨会话项目工作先读 .workflow/STATE.md 与 .workflow/ENTRY.md。\n普通答疑不触发阶段推进；执行者只写任务允许的交付。\n")
        integration = "已创建项目AGENTS入口"
    else:
        integration = "保留既有AGENTS不变；请让Agent读取 .workflow/ENTRY.md，是否合并入口另行决定"
    return {"ok": True, "revision": 0, "entry": f"{META}/STATE.md", "integration": integration}


def active(state):
    tid = state["active_task"]
    require(tid is not None, "没有活动任务")
    return tid, state["tasks"][tid]


def attempt_path(tid, task):
    return f"{META}/tasks/{tid}/attempt-{task['attempt']:02d}"


def contract(root, tid, task):
    errors = immutable_errors(root, task["contract_hashes"])
    require(not errors, "; ".join(errors))
    return read_json(safe(root, f"{META}/tasks/{tid}/task.json", True))


def task_new(root, state, data):
    require(state["active_task"] is None, "先结束或取消当前任务；不并行修改主线")
    for key in ("title", "goal", "authorization", "executor"):
        text(data.get(key), key)
    for key in ("scope", "stop_conditions"):
        strings(data.get(key), key, True)
    strings(data.get("inputs", []), "inputs")
    require(type(data.get("independent")) is bool, "independent须为true或false")
    for p in data["scope"]:
        safe(root, p)
        require(not p.startswith(META), "业务写入范围不能包含流程元数据；交付目录另行分配")
    for p in data.get("inputs", []):
        safe(root, p, True)
    acceptance = data.get("acceptance")
    require(isinstance(acceptance, list) and acceptance, "必须定义验收项")
    ids = []
    for item in acceptance:
        require(isinstance(item, dict), "验收项必须为对象")
        for k in ("id", "criterion", "method"):
            text(item.get(k), k)
        ids.append(item["id"])
    require(len(set(ids)) == len(ids), "验收项ID重复")
    tid = f"T{max([int(k[1:]) for k in state['tasks']] or [0]) + 1:03d}"
    directory = safe(root, f"{META}/tasks/{tid}")
    require(not directory.exists(), "任务目录已占用；可能有未登记写入，请先人工核对，不覆盖")
    data = {k: data[k] for k in ("title", "goal", "authorization", "executor", "scope", "stop_conditions", "independent", "acceptance")}
    data["inputs"] = []  # populated below by caller's checked inputs
    return tid, directory, data


def create_task(root, state, data):
    inputs = data.get("inputs", [])
    tid, directory, spec = task_new(root, state, data)
    spec.update({"inputs": inputs, "id": tid, "protocol": f"{META}/protocol/{VERSION}.md"})
    task = {"status": "ready", "attempt": 1, "contract_hashes": {}, "seals": {}}
    directory.mkdir(parents=True)
    (directory / "attempt-01").mkdir()
    new_json(directory / "task.json", spec)
    md = [f"# {tid} {spec['title']}", "", "固定任务契约。范围改变须新任务，不原地改写。", "",
          "## 目标", spec["goal"], "", "## 授权", spec["authorization"],
          f"执行者：{spec['executor']}；独立验证：{spec['independent']}",
          f"协议：{spec['protocol']}", "", "## 业务写入范围", *spec["scope"],
          "", "## 必读输入", *(inputs or ["无额外必读输入"]), "", "## 验收"]
    md += [f"- {a['id']}：{a['criterion']}；检查：{a['method']}" for a in spec["acceptance"]]
    md += ["", "## 停止条件", *spec["stop_conditions"], "", "## 交付与恢复",
           "当前轮目录见主线状态；report.md及原始证据写入该轮目录。新轮不覆盖旧轮。",
           "报告说明结果、证据、未证明项与现场停止状态；存在不代表验收。",
           "执行者不改变主线状态；指导者读取交付、冻结并审查。", ""]
    (directory / "task.md").write_text("\n".join(md), encoding="utf-8")
    for name in ("task.json", "task.md"):
        task["contract_hashes"][f"{META}/tasks/{tid}/{name}"] = sha(directory / name)
    state["tasks"][tid] = task
    state["active_task"] = tid
    state["summary"] = f"已准备 {tid}：{spec['goal']}"
    state["next_action"] = f"核对任务及实际环境后，由 {spec['executor']} 开始当前阶段。"


def snapshot(root, selections):
    strings(selections, "文件选择", True)
    result = {}
    for selection in selections:
        p = safe(root, selection, True)
        files = [p] if p.is_file() else sorted(p.rglob("*"))
        count = 0
        for f in files:
            rel = f.relative_to(root).as_posix()
            safe(root, rel, True)
            if f.is_file():
                result[rel] = sha(f)
                count += 1
        require(count > 0, f"文件选择为空: {selection}")
    return result


def candidate(root, tid, task, compare=True):
    contract(root, tid, task)
    key = str(task["attempt"])
    seal = task["seals"].get(key)
    require(seal is not None, "本轮尚未提交")
    base = attempt_path(tid, task)
    p = safe(root, f"{base}/submission.json", True)
    require(sha(p) == seal["submission"], "冻结提交记录已变化")
    data = read_json(p)
    errors = immutable_errors(root, data["evidence_hashes"])
    require(not errors, "; ".join(errors))
    if compare:
        require(snapshot(root, data["artifacts"]) == data["artifact_hashes"], "候选成果已变化；旧提交不覆盖当前版本")
    return data, seal["submission"]


def submit(root, state, data):
    tid, task = active(state)
    require(task["status"] in ("ready", "running"), "只有待执行/执行中任务可提交")
    if task["status"] == "ready":
        text(data.get("existing_delivery_checked"), "未登记开始的既有交付须先核查归属、版本及停止状态，并填写existing_delivery_checked")
    spec = contract(root, tid, task)
    strings(data.get("artifacts"), "artifacts", True)
    strings(data.get("evidence"), "evidence", True)
    for key in ("claim", "limits", "next_action"):
        text(data.get(key), key)
    base = attempt_path(tid, task)
    report = safe(root, f"{base}/report.md", True)
    require(report.is_file() and report.read_text(encoding="utf-8-sig").strip(), "报告不能为空")
    for p in data["artifacts"]:
        relative(p)
        require(any(PurePosixPath(p).is_relative_to(PurePosixPath(s)) for s in spec["scope"])
                or PurePosixPath(p).is_relative_to(PurePosixPath(base + "/outputs")), "候选文件不属于授权范围")
    selections = list(dict.fromkeys([f"{base}/report.md"] + data["evidence"]))
    for selection in selections:
        require(safe(root, selection, True).is_file(), "证据须选择具体文件，不能冻结动态目录")
    submission = {"claim": data["claim"], "limits": data["limits"], "artifacts": data["artifacts"],
                  "artifact_hashes": snapshot(root, data["artifacts"]), "evidence_hashes": snapshot(root, selections), "time": now()}
    if data.get("existing_delivery_checked"):
        submission["existing_delivery_checked"] = data["existing_delivery_checked"]
    p = safe(root, f"{base}/submission.json")
    new_json(p, submission)
    task["seals"][str(task["attempt"])] = {"submission": sha(p)}
    task["status"] = "pending_review"
    state["summary"] = f"{tid} 已提交，待主线审查；自验声明：{data['claim']}"
    state["next_action"] = data["next_action"]


def review(root, state, data):
    tid, task = active(state)
    require(task["status"] == "pending_review", "只有待验收任务可审查")
    # Rework must remain possible when the candidate or evidence has drifted.
    decision = data.get("decision")
    require(decision in ("accept", "rework"), "decision应为accept或rework")
    spec = contract(root, tid, task)
    seal = task["seals"][str(task["attempt"])]
    require(data.get("candidate_id") == seal["submission"], "审查绑定的candidate_id不匹配")
    for k in ("reviewer", "findings", "limits", "next_action"):
        text(data.get(k), k)
    strings(data.get("evidence", []), "evidence")
    for selection in data.get("evidence", []):
        require(safe(root, selection, True).is_file(), "审查证据须选择具体文件")
    if decision == "rework":
        next_attempt = dict(task, attempt=task["attempt"] + 1)
        require(not safe(root, attempt_path(tid, next_attempt)).exists(), "返工输出目录已存在，先核对，不覆盖")
    if decision == "accept":
        candidate(root, tid, task)
        if spec["independent"]:
            require(data["reviewer"] != spec["executor"], "本任务要求独立验证，验证者不能是实施者")
            require(bool(data["evidence"]), "独立验证必须提供自己的证据")
        checks = data.get("checks")
        require(isinstance(checks, dict), "accept须逐项核对验收条件")
        require(set(checks) == {a["id"] for a in spec["acceptance"]}, "验收项覆盖不完整")
        for cid, check in checks.items():
            require(isinstance(check, dict) and check.get("result") == "pass", f"{cid} 未通过")
            text(check.get("finding"), f"{cid}.finding")
            strings(check.get("evidence"), f"{cid}.evidence", True)
            known = set(data.get("evidence", [])) | set(read_json(safe(root, attempt_path(tid, task) + "/submission.json"))["evidence_hashes"])
            require(set(check["evidence"]) <= known, f"{cid}引用未冻结的证据")
    report = dict(data)
    report["accepted_by"] = state["owner"]
    report["time"] = now()
    report["evidence_hashes"] = snapshot(root, data["evidence"]) if data.get("evidence") else {}
    p = safe(root, f"{attempt_path(tid, task)}/review.json")
    new_json(p, report)
    seal["review"] = sha(p)
    if decision == "accept":
        task["status"] = "accepted"
        state["active_task"] = None
        state["last_accepted"] = tid
    else:
        task["attempt"] += 1
        safe(root, attempt_path(tid, task)).mkdir()
        task["status"] = "ready"
    state["summary"] = f"{tid} 审查结论：{data['findings']}\n证据边界：{data['limits']}"
    state["next_action"] = data["next_action"]


def handoff(root, state, data, new_owner):
    text(new_owner, "新主线维护者")
    require(new_owner != state["owner"], "新旧维护者相同，无需交接")
    for k in ("reason", "next_action"):
        text(data.get(k), k)
    tid = state["active_task"]
    if tid and state["tasks"][tid]["status"] != "ready":
        for k in ("completed", "remaining", "processes", "budget", "worktree", "ruled_out", "stop_conditions"):
            text(data.get(k), k)
    p = safe(root, f"{META}/history/handoff-{state['revision'] + 1:04d}.json")
    new_json(p, {"from": state["owner"], "to": new_owner, "task": tid, "time": now(), **data})
    state["owner"] = new_owner
    state["handoff"] = p.relative_to(root).as_posix()
    state["handoff_task"] = tid
    state["next_action"] = data["next_action"]


def inspect(root, state):
    errors = immutable_errors(root, state["runtime_hashes"])
    warnings = []
    if not view_matches(root, state):
        errors.append("STATE.md与权威状态失步；sync可保留现有视图并重建")
    for tid, task in state["tasks"].items():
        errors += immutable_errors(root, task["contract_hashes"])
        for number, seals in task["seals"].items():
            base = f"{META}/tasks/{tid}/attempt-{int(number):02d}"
            for key, filename in (("submission", "submission.json"), ("review", "review.json")):
                if key not in seals:
                    continue
                path = f"{base}/{filename}"
                e = immutable_errors(root, {path: seals[key]})
                errors += e
                if not e:
                    frozen = read_json(safe(root, path))
                    drift = immutable_errors(root, frozen.get("evidence_hashes", {}))
                    current = (tid in (state.get("active_task"), state.get("last_accepted"))
                               and int(number) == task["attempt"] and task["status"] != "cancelled")
                    if current:
                        errors += drift
                    else:
                        warnings += [f"历史证据 {tid}/attempt-{int(number):02d} 不再匹配：{d}" for d in drift]
        if tid in (state.get("active_task"), state.get("last_accepted")) and task["status"] in ("pending_review", "accepted"):
            try:
                candidate(root, tid, task)
            except (FlowError, OSError) as exc:
                if task["status"] == "accepted":
                    warnings.append(f"{tid}历史接受不覆盖当前文件：{exc}")
                else:
                    errors.append(str(exc))
    tasks_dir = safe(root, f"{META}/tasks")
    if tasks_dir.exists():
        for p in tasks_dir.iterdir():
            if p.name not in state["tasks"]:
                errors.append(f"发现未登记任务目录 {p.name}；可能为中断写入，需核对")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "boundary": "只检查记录一致性和文件内容，不证明测试执行、身份真实或用户验收。"}


def status(root, state):
    tid = state["active_task"]
    task = state["tasks"].get(tid)
    result = {k: state[k] for k in ("name", "version", "revision", "owner", "goal", "summary", "next_action", "active_task", "last_accepted")}
    result["entry"] = f"{META}/STATE.md"
    if task:
        result["task_status"] = task["status"]
        result["attempt"] = task["attempt"]
        result["task"] = f"{META}/tasks/{tid}/task.md"
        base = attempt_path(tid, task)
        result["output"] = base
        result["report_found"] = safe(root, base + "/report.md").is_file()
        result["candidate_id"] = task["seals"].get(str(task["attempt"]), {}).get("submission")
        if result["report_found"] and task["status"] in ("running", "ready"):
            result["notice"] = "发现报告，尚未登记提交；先核对已有交付，禁止重复派发。"
        if task.get("blocker"):
            result["blocker"] = task["blocker"]
    if state.get("handoff") and state.get("handoff_task") == state["active_task"]:
        result["handoff"] = state["handoff"]
    checked = []
    for cid in dict.fromkeys([state.get("active_task"), state.get("last_accepted")]):
        if not cid:
            continue
        current = state["tasks"][cid]
        if current["status"] in ("pending_review", "accepted"):
            try:
                candidate(root, cid, current)
                checked.append({"task": cid, "files_match": True})
            except (FlowError, OSError) as exc:
                checked.append({"task": cid, "files_match": False, "warning": str(exc)})
    if checked:
        result["current_file_checks"] = checked
    return result


def operate(root, command, data, actor, expected, new_owner=None):
    with edit(root, actor, expected, allow_stale=command == "sync") as state:
        if command == "task":
            create_task(root, state, data)
        elif command == "start":
            tid, task = active(state)
            contract(root, tid, task)
            require(task["status"] == "ready", "任务不在待执行状态")
            require(not safe(root, attempt_path(tid, task) + "/report.md").exists(), "发现既有报告，先核对；禁止重复开始")
            task["status"] = "running"
            state["next_action"] = "执行当前任务；交付后由主线读取证据并提交审查。"
        elif command == "submit":
            submit(root, state, data)
        elif command == "review":
            review(root, state, data)
        elif command == "note":
            for k in ("summary", "next_action"):
                state[k] = text(data.get(k), k)
            p = safe(root, f"{META}/history/note-{state['revision'] + 1:04d}.json")
            new_json(p, {"time": now(), "previous_summary": load(root)["summary"], **data})
        elif command == "handoff":
            handoff(root, state, data, new_owner)
        elif command == "block":
            _, task = active(state)
            require(task["status"] != "blocked", "已经阻塞；不要重复登记")
            task["resume_status"] = task["status"]
            task["status"] = "blocked"
            task["blocker"] = text(data.get("reason"), "reason")
            state["next_action"] = text(data.get("next_action"), "next_action")
        elif command == "resume":
            _, task = active(state)
            require(task["status"] == "blocked", "任务未阻塞")
            text(data.get("reason"), "解除阻塞依据")
            task["status"] = task.pop("resume_status")
            task.pop("blocker")
            state["next_action"] = text(data.get("next_action"), "next_action")
            new_json(safe(root, f"{META}/history/resume-{state['revision'] + 1:04d}.json"), data)
        elif command == "cancel":
            tid, task = active(state)
            task["status"] = "cancelled"
            state["active_task"] = None
            state["summary"] = f"{tid}已取消：{text(data.get('reason'), 'reason')}"
            state["next_action"] = text(data.get("next_action"), "next_action")
            new_json(safe(root, f"{META}/history/cancel-{state['revision'] + 1:04d}.json"), data)
        elif command == "sync":
            p = safe(root, f"{META}/STATE.md")
            if p.exists() and not view_matches(root, state):
                backup = safe(root, f"{META}/history/view-before-sync-{state['revision'] + 1:04d}.md")
                require(not backup.exists(), "视图备份已存在，先核对中断操作")
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, backup)
        else:
            raise FlowError(f"未知变更命令 {command}")
    return {"ok": True, **status(root, load(root))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="已存在的项目根目录")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="初始化项目内工作流，保留已有AGENTS")
    p.add_argument("--data", required=True)
    p.add_argument("--actor", required=True)
    for cmd in ("status", "check", "brief"):
        sub.add_parser(cmd)
    for cmd in ("task", "start", "submit", "review", "note", "handoff", "block", "resume", "cancel", "sync"):
        p = sub.add_parser(cmd)
        p.add_argument("--actor", required=True)
        p.add_argument("--expect", type=int, required=True, help="最近读取的revision，避免过期写入")
        if cmd not in ("start", "sync"):
            p.add_argument("--data", required=True, help="Agent准备的JSON输入文件")
        if cmd == "handoff":
            p.add_argument("--to", required=True)
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).absolute()
        require(root.is_dir() and not linked(root), "项目根目录不存在或为链接")
        root = root.resolve()
        data = read_json(args.data) if getattr(args, "data", None) else {}
        if args.command == "init":
            result = init(root, data, args.actor)
        elif args.command in ("status", "check", "brief"):
            state = load(root)
            result = inspect(root, state) if args.command == "check" else status(root, state)
            if args.command == "brief":
                result["read_first"] = [f"{META}/STATE.md", f"{META}/PROJECT.md", f"{META}/protocol/{VERSION}.md"]
                if state["active_task"]:
                    result["read_first"].append(result["task"])
                if result.get("handoff"):
                    result["read_first"].append(result["handoff"])
                result["instructions"] = "核对文件与现场；只读必要输入，不递归加载历史。不因报告存在自动接受，不重复派发。"
        else:
            result = operate(root, args.command, data, args.actor, args.expect, getattr(args, "to", None))
        print(encode(result), end="")
        return 0 if result.get("ok", True) else 1
    except (FlowError, OSError, KeyError, TypeError, UnicodeError) as exc:
        print(encode({"ok": False, "error": str(exc)}), end="")
        return 1


if __name__ == "__main__":
    sys.exit(main())
