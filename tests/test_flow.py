"""Fault-focused contract tests; these do not simulate authenticated agents."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/mainline/scripts/flow.py"
spec = importlib.util.spec_from_file_location("flow", SCRIPT)
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="project-flow-tests-")
        self.root = Path(self.temp.name) / "中文 项目"
        self.root.mkdir()
        self.project = {"name": "测试项目", "goal": "可验证交付", "acceptance": ["满足阶段验收"], "constraints": ["只允许本地文件，不发布"]}
        flow.init(self.root, self.project, "guide")

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return flow.load(self.root)

    def run_op(self, cmd, data=None, actor=None, expected=None, new_owner=None):
        state = self.state()
        return flow.operate(self.root, cmd, data or {}, actor or state["owner"],
                            state["revision"] if expected is None else expected, new_owner)

    def task(self, independent=False, executor="worker", scope=None):
        return self.run_op("task", {"title": "阶段", "goal": "交付可核查成果", "scope": scope or ["outputs"], "inputs": [],
            "authorization": "本地写入，不发布", "executor": executor, "independent": independent,
            "acceptance": [{"id": "A1", "criterion": "输出包含预期结果", "method": "读取并复算"}],
            "stop_conditions": ["不可复算则停止"]})

    def write(self, path, content="evidence"):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def working(self, independent=False, executor="worker"):
        self.task(independent, executor)
        self.run_op("start")
        self.write("outputs/result.txt", "4")
        self.write(".workflow/tasks/T001/attempt-01/report.md", "结果为4，依据为2+2；未对外发布")
        self.write(".workflow/tasks/T001/attempt-01/check.txt", "2 + 2 = 4")

    def submitted(self, independent=False, executor="worker"):
        self.working(independent, executor)
        return self.run_op("submit", {"artifacts": ["outputs"], "evidence": [".workflow/tasks/T001/attempt-01/check.txt"],
            "claim": "完成", "limits": "仅本地", "next_action": "核查证据"})

    def review_data(self, reviewer="guide", decision="accept"):
        s = flow.status(self.root, self.state())
        return {"decision": decision, "candidate_id": s["candidate_id"], "reviewer": reviewer,
                "findings": "复算一致", "limits": "仅冻结文件", "next_action": "交用户使用", "evidence": [],
                "checks": {"A1": {"result": "pass", "finding": "2+2与输出一致",
                           "evidence": [".workflow/tasks/T001/attempt-01/check.txt"]}}}

    def test_init_preserves_existing_business_and_agent_files(self):
        another = self.root.parent / "existing"
        another.mkdir()
        (another / "AGENTS.md").write_bytes(b"existing instructions")
        (another / "app.py").write_bytes(b"user work")
        result = flow.init(another, self.project, "guide")
        self.assertEqual((another / "AGENTS.md").read_bytes(), b"existing instructions")
        self.assertEqual((another / "app.py").read_bytes(), b"user work")
        self.assertIn("保留", result["integration"])

    def test_reinitialization_refuses_without_changes(self):
        before = (self.root / ".workflow/state.json").read_bytes()
        with self.assertRaises(flow.FlowError):
            flow.init(self.root, self.project, "new")
        self.assertEqual(before, (self.root / ".workflow/state.json").read_bytes())

    def test_malformed_init_leaves_no_workflow(self):
        target = self.root.parent / "bad-init"
        target.mkdir()
        with self.assertRaises(flow.FlowError):
            flow.init(target, {"name": "x"}, "guide")
        self.assertEqual(list(target.iterdir()), [])

    def test_owner_and_stale_revision_guard(self):
        with self.assertRaisesRegex(flow.FlowError, "主线"):
            self.run_op("note", {"summary": "x", "next_action": "y"}, actor="worker")
        self.run_op("note", {"summary": "x", "next_action": "y"})
        with self.assertRaisesRegex(flow.FlowError, "修订"):
            self.run_op("note", {"summary": "z", "next_action": "y"}, expected=0)
        self.assertEqual(self.state()["summary"], "x")

    def test_writer_lock_prevents_overlap(self):
        p = self.write(".workflow/write.lock", "other owner")
        with self.assertRaisesRegex(flow.FlowError, "写入锁"):
            self.run_op("note", {"summary": "x", "next_action": "y"})
        self.assertEqual(p.read_text(), "other owner")

    def test_report_presence_neither_accepts_nor_resubmits(self):
        self.working()
        state = self.state()
        result = flow.status(self.root, state)
        self.assertEqual(result["task_status"], "running")
        self.assertTrue(result["report_found"])
        self.assertIn("禁止重复", result["notice"])
        with self.assertRaises(flow.FlowError):
            self.run_op("start")
        with self.assertRaises(flow.FlowError):
            self.run_op("review", self.review_data())

    def test_full_lifecycle_and_compact_brief(self):
        result = self.submitted()
        self.assertEqual(result["task_status"], "pending_review")
        self.run_op("review", self.review_data())
        self.assertIsNone(self.state()["active_task"])
        self.assertEqual(self.state()["last_accepted"], "T001")
        self.assertTrue(flow.inspect(self.root, self.state())["ok"])
        self.assertLess(len((self.root / ".workflow/STATE.md").read_text(encoding="utf-8").splitlines()), 50)

    def test_ready_existing_report_can_be_adopted_after_explicit_inspection(self):
        self.task()
        self.write("outputs/result.txt", "4")
        self.write(".workflow/tasks/T001/attempt-01/report.md", "已执行，原始证据可核对")
        self.write(".workflow/tasks/T001/attempt-01/check.txt", "2+2=4")
        data = {"artifacts": ["outputs"], "evidence": [".workflow/tasks/T001/attempt-01/check.txt"],
                "claim": "完成", "limits": "仅离线", "next_action": "审查"}
        with self.assertRaises(flow.FlowError):
            self.run_op("start")
        with self.assertRaisesRegex(flow.FlowError, "既有交付"):
            self.run_op("submit", data)
        data["existing_delivery_checked"] = "已核对T001来源、成果版本和已停止状态，未重新执行"
        self.run_op("submit", data)
        self.assertEqual(self.state()["tasks"]["T001"]["status"], "pending_review")

    def test_missing_acceptance_evidence_cannot_pass(self):
        self.submitted()
        data = self.review_data()
        data["checks"] = {}
        with self.assertRaisesRegex(flow.FlowError, "覆盖"):
            self.run_op("review", data)
        data = self.review_data()
        data["checks"]["A1"]["evidence"] = []
        with self.assertRaises(flow.FlowError):
            self.run_op("review", data)
        self.assertEqual(self.state()["tasks"]["T001"]["status"], "pending_review")

    def test_wrong_candidate_id_rejected(self):
        self.submitted()
        data = self.review_data()
        data["candidate_id"] = "wrong"
        with self.assertRaisesRegex(flow.FlowError, "candidate_id"):
            self.run_op("review", data)

    def test_changed_artifact_rejected_before_accept(self):
        self.submitted()
        self.write("outputs/result.txt", "5")
        with self.assertRaisesRegex(flow.FlowError, "成果已变化"):
            self.run_op("review", self.review_data())

    def test_added_artifact_in_selected_directory_rejected(self):
        self.submitted()
        self.write("outputs/unreviewed.txt", "new behavior")
        with self.assertRaisesRegex(flow.FlowError, "成果已变化"):
            self.run_op("review", self.review_data())

    def test_deleted_artifact_rejected(self):
        self.submitted()
        (self.root / "outputs/result.txt").unlink()
        with self.assertRaises(flow.FlowError):
            self.run_op("review", self.review_data())

    def test_tampered_report_rejected(self):
        self.submitted()
        self.write(".workflow/tasks/T001/attempt-01/report.md", "actually no test")
        with self.assertRaisesRegex(flow.FlowError, "冻结文件"):
            self.run_op("review", self.review_data())

    def test_tampered_candidate_manifest_rejected(self):
        self.submitted()
        self.write(".workflow/tasks/T001/attempt-01/submission.json", "{}")
        with self.assertRaisesRegex(flow.FlowError, "提交记录"):
            self.run_op("review", self.review_data())

    def test_accepted_version_change_is_disclosed_not_silently_inherited(self):
        self.submitted()
        self.run_op("review", self.review_data())
        self.write("outputs/result.txt", "new version")
        report = flow.inspect(self.root, self.state())
        self.assertTrue(report["warnings"])
        self.assertEqual(self.state()["tasks"]["T001"]["status"], "accepted")

    def test_independent_gate_rejects_implementer(self):
        self.submitted(independent=True)
        with self.assertRaisesRegex(flow.FlowError, "实施者"):
            self.run_op("review", self.review_data(reviewer="worker"))

    def test_independent_acceptance_requires_separate_evidence(self):
        self.submitted(independent=True)
        data = self.review_data(reviewer="verifier")
        with self.assertRaisesRegex(flow.FlowError, "自己的证据"):
            self.run_op("review", data)
        path = ".workflow/tasks/T001/attempt-01/independent.txt"
        self.write(path, "独立复算为4；候选=" + data["candidate_id"])
        data["evidence"] = [path]
        data["checks"]["A1"]["evidence"] = [path]
        self.run_op("review", data)
        self.assertTrue(flow.inspect(self.root, self.state())["ok"])

    def test_simple_stage_can_use_same_guide_and_executor(self):
        self.submitted(executor="guide")
        self.run_op("review", self.review_data(reviewer="guide"))
        self.assertEqual(self.state()["tasks"]["T001"]["status"], "accepted")

    def test_rework_retains_previous_attempt_and_contract(self):
        self.submitted()
        old = self.root / ".workflow/tasks/T001/attempt-01/submission.json"
        before = old.read_bytes()
        self.write("outputs/result.txt", "broken")
        self.run_op("review", self.review_data(decision="rework"))
        self.assertEqual(old.read_bytes(), before)
        self.assertTrue((self.root / ".workflow/tasks/T001/attempt-02").is_dir())
        self.assertEqual(self.state()["tasks"]["T001"]["status"], "ready")
        self.run_op("start")

    def test_evidence_drift_does_not_permanently_block_valid_rework(self):
        self.submitted()
        self.write(".workflow/tasks/T001/attempt-01/check.txt", "historical evidence changed")
        with self.assertRaises(flow.FlowError):
            self.run_op("review", self.review_data())
        self.run_op("review", self.review_data(decision="rework"))
        self.run_op("start")
        base = ".workflow/tasks/T001/attempt-02"
        self.write(base + "/report.md", "新一轮独立重算当前成果")
        self.write(base + "/check.txt", "2+2=4")
        self.run_op("submit", {"artifacts": ["outputs"], "evidence": [base + "/check.txt"],
                               "claim": "新证据重验", "limits": "仅本地", "next_action": "核对新轮"})
        data = self.review_data()
        data["checks"]["A1"]["evidence"] = [base + "/check.txt"]
        self.run_op("review", data)
        report = flow.inspect(self.root, self.state())
        self.assertTrue(report["ok"], report)
        self.assertTrue(any("attempt-01" in w for w in report["warnings"]))

    def test_status_reveals_accepted_artifact_drift(self):
        self.submitted()
        self.run_op("review", self.review_data())
        self.write("outputs/result.txt", "5")
        result = flow.status(self.root, self.state())
        self.assertFalse(result["current_file_checks"][0]["files_match"])

    def test_finished_stage_does_not_force_stale_handoff_context(self):
        self.submitted()
        data = {k: "已核对" for k in ("reason", "next_action", "completed", "remaining", "processes", "budget", "worktree", "ruled_out", "stop_conditions")}
        self.run_op("handoff", data, new_owner="guide2")
        handoff_path = self.state()["handoff"]
        self.assertIn("handoff", flow.status(self.root, self.state()))
        self.run_op("review", self.review_data())
        self.assertNotIn("handoff", flow.status(self.root, self.state()))
        self.assertTrue((self.root / handoff_path).exists())

    def test_initialized_entry_has_working_command_reference(self):
        import re
        p = self.root / ".workflow/ENTRY.md"
        links = re.findall(r'\[[^\]]+\]\(([^)]+)\)', p.read_text(encoding="utf-8"))
        self.assertTrue(links)
        for link in links:
            if "://" not in link:
                self.assertTrue((p.parent / link).is_file(), link)

    def test_block_resume_restores_previous_phase(self):
        self.submitted()
        self.run_op("block", {"reason": "等待原始数据", "next_action": "获取数据"})
        self.assertEqual(self.state()["tasks"]["T001"]["status"], "blocked")
        self.run_op("resume", {"reason": "数据已到", "next_action": "继续审查"})
        self.assertEqual(self.state()["tasks"]["T001"]["status"], "pending_review")

    def test_midstage_handoff_requires_recovery_facts(self):
        self.working()
        data = {"reason": "换会话", "next_action": "接手"}
        with self.assertRaises(flow.FlowError):
            self.run_op("handoff", data, new_owner="guide2")
        for key in ("completed", "remaining", "processes", "budget", "worktree", "ruled_out", "stop_conditions"):
            data[key] = "未知，接手核对"
        self.run_op("handoff", data, new_owner="guide2")
        with self.assertRaisesRegex(flow.FlowError, "主线"):
            self.run_op("note", {"summary": "旧会话", "next_action": "x"}, actor="guide")
        self.assertEqual(self.state()["owner"], "guide2")

    def test_cancel_then_new_task_has_distinct_id(self):
        self.task()
        self.run_op("cancel", {"reason": "目标改变", "next_action": "新契约"})
        self.task()
        self.assertEqual(self.state()["active_task"], "T002")
        self.assertTrue((self.root / ".workflow/tasks/T001/task.md").is_file())

    def test_handwritten_view_preserved_on_sync(self):
        p = self.write(".workflow/STATE.md", "user-added note")
        with self.assertRaisesRegex(flow.FlowError, "失步"):
            self.run_op("note", {"summary": "x", "next_action": "y"})
        self.run_op("sync")
        backups = list((self.root / ".workflow/history").glob("view-before-sync-*.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "user-added note")
        self.assertTrue(flow.view_matches(self.root, self.state()))

    def test_missing_view_after_interrupted_render_can_sync(self):
        (self.root / ".workflow/STATE.md").unlink()
        self.run_op("sync")
        self.assertTrue(flow.view_matches(self.root, self.state()))

    def test_scope_escape_and_windows_absolute_path_rejected(self):
        for value in ("../outside", "/tmp/outside", "C:/outside", "outputs/../outside", "outputs\\x"):
            with self.subTest(value=value), self.assertRaises(flow.FlowError):
                self.task(scope=[value])
        self.assertFalse((self.root / ".workflow/tasks").exists())

    def test_candidate_outside_declared_scope_rejected(self):
        self.working()
        self.write("other.txt", "not authorized")
        with self.assertRaisesRegex(flow.FlowError, "授权范围"):
            self.run_op("submit", {"artifacts": ["other.txt"], "evidence": [".workflow/tasks/T001/attempt-01/check.txt"],
                "claim": "完成", "limits": "none", "next_action": "review"})

    def test_frozen_task_or_runtime_changes_detected(self):
        self.task()
        self.write(".workflow/tasks/T001/task.json", "{}")
        with self.assertRaisesRegex(flow.FlowError, "冻结文件"):
            self.run_op("start")
        self.assertFalse(flow.inspect(self.root, self.state())["ok"])

    def test_orphan_task_directory_is_not_overwritten(self):
        self.write(".workflow/tasks/T001/unknown.txt", "old work")
        with self.assertRaisesRegex(flow.FlowError, "已占用"):
            self.task()
        self.assertFalse(flow.inspect(self.root, self.state())["ok"])

    def test_copied_project_runs_without_original_installation(self):
        self.submitted()
        moved = self.root.parent / "relocated 中文"
        shutil.copytree(self.root, moved)
        p = subprocess.run([sys.executable, "-B", str(moved / ".workflow/runtime/flow.py"), "--root", str(moved), "brief"],
                           capture_output=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        result = json.loads(p.stdout)
        self.assertEqual(result["task_status"], "pending_review")
        self.assertLessEqual(len(result["read_first"]), 5)
        self.assertTrue((moved / ".workflow/COMMANDS.md").is_file())

    def test_cli_input_error_is_json_and_no_traceback(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = flow.main(["--root", str(self.root), "note", "--actor", "guide", "--expect", "0", "--data", "missing.json"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(buf.getvalue())["ok"])

    def test_symlink_candidate_is_rejected(self):
        outside = self.write("outside.txt", "do not read through link")
        link = self.root / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("host does not grant symlink creation")
        with self.assertRaisesRegex(flow.FlowError, "符号链接"):
            flow.snapshot(self.root, ["link.txt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
