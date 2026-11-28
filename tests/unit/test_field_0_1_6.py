"""Regression tests for the Windows / bist-bot field report fixed in 0.1.6 (INC-009 … INC-016)."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, git, requirements, task, tmpdir, write

import devpipeline as dp
import quality_gate
import run_checks
import state_manager as sm
import validate_contracts as vc
from dpcore.util import read_json

from test_state_gate import GateFixture, new_run

CLI = [sys.executable, str(ROOT / "scripts" / "devpipeline.py")]


def cli(*args, stdin=None):
    # own registry: `context` registers an active run, which must not leak into the hook-guard tests
    env = {**os.environ, "DEVPIPELINE_STATE_DIR": str(tmpdir())}
    out = subprocess.run(CLI + list(args), input=stdin, capture_output=True, text=True, encoding="utf-8", env=env)
    return json.loads(out.stdout)


def python_project(d: Path, pyproject: str = "", tests=None):
    write(d / "pyproject.toml", "[project]\nname = 'bot'\nversion = '0.1.0'\n" + pyproject)
    write(d / "requirements.txt", "requests==2.32.3\n")
    for rel in tests or []:
        write(d / rel, "def test_ok():\n    assert True\n")


class StartupTests(unittest.TestCase):
    def test_prepare_reads_request_from_stdin_dash_and_copies_workflow(self):
        d = tmpdir() / "kız-projesi"
        res = cli("prepare", "--project", str(d), "--mode", "build", "--new", "--request-file", "-", stdin="Borsa botu yap: çıkış kodları\n")
        self.assertTrue(res["ok"], res)
        self.assertIn("çıkış", (sm.run_dir(d, res["run_id"]) / "request.txt").read_text(encoding="utf-8"))
        script = Path(res["workflow_script"])
        self.assertTrue(script.is_file())
        self.assertTrue(str(script).startswith(str(sm.run_dir(d.resolve(), res["run_id"]))), "copy lives inside the project")
        self.assertEqual(script.read_text(), (ROOT / "workflows" / "build-project.js").read_text())

    def test_missing_request_file_is_a_clear_error(self):
        res = cli("prepare", "--project", str(tmpdir() / "x"), "--mode", "build", "--new", "--request-file", "/no/such/file")
        self.assertFalse(res["ok"])
        self.assertIn("--request-file -", res["error"])


class ResumeLimitTests(unittest.TestCase):
    def test_resume_rereads_limits_from_config_json(self):
        project, run = new_run(profile="fast")
        self.assertEqual(sm.load_manifest(project, run)["config"]["max_agent_calls"], 40)
        with sm.mutate(project, run) as m:
            m["counters"]["agent_calls"] = 40
        sm.transition(project, run, "ANALYZING")
        sm.transition(project, run, "BUDGET_EXHAUSTED", "agent call limit (40) reached")
        refused = cli("resume", "--project", str(project), "--run", run)
        self.assertFalse(refused["ok"])
        self.assertIn("max_agent_calls", refused["error"])
        cfg_path = project / ".devpipeline" / "config.json"
        pcfg = read_json(cfg_path)
        pcfg["overrides"] = {"max_agent_calls": 80}
        write(cfg_path, pcfg)
        res = cli("resume", "--project", str(project), "--run", run)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["config_changes"], {"max_agent_calls": 80})
        self.assertEqual(sm.load_manifest(project, run)["config"]["max_agent_calls"], 80)
        ctx = cli("context", "--project", str(project), "--run", run)
        self.assertEqual(ctx["config"]["max_agent_calls"], 80, "the workflow reads the refreshed limit")
        events = (sm.run_dir(project, run) / "events.jsonl").read_text()
        self.assertIn("CONFIG_REFRESHED", events)

    def test_resume_keeps_prepare_time_cli_overrides(self):
        project, run = new_run(overrides={"max_parallel_agents": 1})
        sm.transition(project, run, "ANALYZING")
        sm.transition(project, run, "BLOCKED", "x")
        res = cli("resume", "--project", str(project), "--run", run, "--overrides-json", '{"max_repair_iterations": 7}')
        self.assertTrue(res["ok"], res)
        cfg = sm.load_manifest(project, run)["config"]
        self.assertEqual((cfg["max_parallel_agents"], cfg["max_repair_iterations"]), (1, 7))


class CheckRunnerTests(unittest.TestCase):
    def test_secret_scan_is_builtin_and_never_hits_the_shell_filter(self):
        d = tmpdir()
        write(d / "a.py", "x = 1\n")
        chk = next(c for c in run_checks.discover(d) if c["name"] == "secret-scan")
        chk = {**chk, "command": "python3 'C:\\Users\\Emin\\devpipeline\\scripts\\redact_secrets.py' scan ."}  # what 0.1.5 produced on Windows
        rec = run_checks.execute(chk, d, d / "logs", "t", 60, "fp")
        self.assertEqual((rec["status"], rec["exit_code"]), ("PASS", 0), rec)

    def test_pip_audit_is_scoped_and_declared_audit_replaces_it(self):
        d = tmpdir()
        python_project(d)
        real_which = run_checks.shutil.which
        with mock.patch.object(run_checks.shutil, "which", lambda x: "/usr/bin/pip-audit" if x == "pip-audit" else real_which(x)):
            found = {c["name"]: c for c in run_checks.discover(d)}
            self.assertEqual(found["dependency-audit:python:root"]["command"], "pip-audit -r requirements.txt")
            arch = {"checks": [{"name": "security-pip-audit", "kind": "security", "command": "pip-audit -r requirements.txt --strict"}]}
            names = [c["name"] for c in run_checks.discover(d, arch)]
        self.assertIn("security-pip-audit", names)
        self.assertNotIn("dependency-audit:python:root", names, "no second, unscoped audit")

    def test_python_project_can_declare_checks_in_files_it_owns(self):
        d = tmpdir()
        python_project(d, '[tool.devpipeline.checks]\ne2e = "python -m pytest -q tests/e2e"\na11y-smoke = { kind = "accessibility", command = "python -m pytest -q tests/ui" }\n',
                       tests=["tests/integration/test_api.py"])
        with mock.patch.object(run_checks.importlib.util, "find_spec", lambda name: object() if name == "pytest" else None):
            by_kind = {c["kind"]: c for c in run_checks.discover(d)}
        self.assertEqual(by_kind["e2e"]["command"], "python -m pytest -q tests/e2e")
        self.assertEqual(by_kind["accessibility"]["name"], "python:root:a11y-smoke")
        self.assertTrue(by_kind["integration"]["command"].endswith("tests/integration"), by_kind["integration"])


class ResultNormalisationTests(unittest.TestCase):
    def test_contract_change_request_shapes_are_mapped(self):
        f = write(tmpdir() / "r.json", {"status": "PARTIAL", "summary": "s", "changed_files": [], "validation": [], "issues": [],
                                         "contract_change_requests": [{"title": "declare e2e check", "summary": "add checks[] entry",
                                                                       "file": ".devpipeline/architecture.json", "why": "not owned"}]})
        res = dp.normalize_result(f, "run-1", "TASK-921-R2", "backend-engineer")
        check = vc.check_result(res, "TASK-921-R2", "backend-engineer", "run-1")
        self.assertTrue(check["valid"], check["errors"])
        ccr = res["contract_change_requests"][0]
        self.assertEqual(ccr["contract"], ".devpipeline/architecture.json")
        self.assertEqual(ccr["reason"], "not owned")
        self.assertIn("add checks[] entry", ccr["change"])

    def test_expected_nonzero_exit_is_a_pass_and_unexplained_one_does_not_discard_work(self):
        f = write(tmpdir() / "r.json", {"status": "COMPLETED", "summary": "s", "changed_files": [], "issues": [], "validation": [
            {"name": "cli-error-date", "status": "PASS", "exit_code": 2, "expected_exit_code": 2, "evidence_path": "l.log"},
            {"name": "cli-reset-abort", "status": "PASS", "exit_code": 1, "evidence_path": "l.log"}]})
        res = dp.normalize_result(f, "run-1", "TASK-013", "backend-engineer")
        self.assertTrue(vc.check_result(res, "TASK-013", "backend-engineer", "run-1")["valid"])
        self.assertEqual([v["status"] for v in res["validation"]], ["PASS", "ERROR"])
        bad = dict(res, validation=[{"name": "x", "status": "PASS", "exit_code": 2}])
        self.assertFalse(vc.check_result(bad)["valid"], "the validator itself still rejects an unexplained claim")

    def test_missing_result_is_salvaged(self):
        project, run = new_run()
        wt = tmpdir()
        t = task("TASK-004", worktree=str(wt))
        rpath = sm.run_dir(project, run) / "agent-results" / "TASK-004.json"
        write(wt / ".devpipeline" / "runs" / run / "agent-results" / "TASK-004.json", {"status": "COMPLETED", "summary": "relative write"})
        self.assertIn("moved", dp.salvage_result(project, run, t, rpath, None))
        self.assertEqual(read_json(rpath)["summary"], "relative write")
        rpath.unlink()
        self.assertIn("returned", dp.salvage_result(project, run, t, rpath, {"status": "COMPLETED", "summary": "done: 4 files"}))
        res = dp.normalize_result(rpath, run, "TASK-004", "backend-engineer")
        self.assertTrue(vc.check_result(res, "TASK-004", "backend-engineer", run)["valid"])
        rpath.unlink()
        self.assertIsNone(dp.salvage_result(project, run, t, rpath, None), "a null return is never turned into a result")


class GateTests(unittest.TestCase):
    def test_relative_log_path_counts_as_evidence(self):
        f = GateFixture()
        f.checks(); f.final()
        write(f.rd / "logs" / "bandit.log", "No issues identified.")
        write(f.rd / "test-reports/code_qa.json", {"run_id": f.run, "agent": "code-qa-engineer", "kind": "code_qa", "source_fingerprint": f.fps()["prod"],
              "status": "PASS", "checks": [{"name": "bandit", "status": "PASS", "evidence_type": "executed", "exit_code": 0, "log_path": "logs/bandit.log"}],
              "issues": [], "summary": "s"})
        _, g = f.gate()
        self.assertEqual(g["code_qa"]["status"], "PASS", g["code_qa"]["reason"])

    def test_report_gate_reason_names_what_failed(self):
        f = GateFixture()
        f.checks(); f.final()
        log = write(f.rd / "logs" / "x.log", "1 failed")
        write(f.rd / "test-reports/code_qa.json", {"run_id": f.run, "agent": "code-qa-engineer", "kind": "code_qa", "source_fingerprint": f.fps()["prod"],
              "status": "PASS", "checks": [{"name": "lint", "status": "FAIL", "evidence_type": "executed", "exit_code": 1, "log_path": str(log)},
                                           {"name": "unit", "status": "PASS", "evidence_type": "executed", "exit_code": 0, "log_path": str(log)}],
              "issues": [], "summary": "s"})
        _, g = f.gate()
        self.assertEqual(g["code_qa"]["status"], "FAIL")
        self.assertIn("lint: FAIL", g["code_qa"]["reason"])
        self.assertNotIn("report PASS", g["code_qa"]["reason"])

    def test_missing_check_issue_says_how_to_add_it_and_owns_the_files(self):
        extra = {"id": "AC-002", "description": "the bot talks to the broker API", "verification_method": "integration"}
        f = GateFixture(req=requirements(extra_ac=extra))
        f.checks(); f.qa(); f.final()
        res, g = f.gate()
        self.assertEqual(g["integration_tests"]["status"], "NOT_RUN")
        issue = next(read_json(p) for p in (f.rd / "issues").glob("ISSUE-*.json") if read_json(p).get("check_name") == "gate:integration_tests")
        self.assertIn("test:integration", issue["description"])
        self.assertIn("package.json", issue["affected_files"])
        write(f.wt / "pyproject.toml", "[project]\nname = 'bot'\n")  # a Python project is told about pyproject / tests dirs
        how, files = quality_gate._how_to_add_check(["integration"], f.wt)
        self.assertIn("[tool.devpipeline.checks] integration", how)
        self.assertIn("tests/integration/", files)

    def test_contract_change_request_becomes_architect_issue_until_decided(self):
        f = GateFixture()
        f.checks(); f.qa(); f.final()
        with sm.mutate(f.project, f.run) as m:
            m["selected_agents"].append({"agent": "software-architect", "reason": "t"})
        write(f.rd / "agent-results" / "TASK-001.json", {"run_id": f.run, "task_id": "TASK-001", "agent": "backend-engineer", "status": "PARTIAL",
              "changed_files": [], "validation": [], "issues": [], "summary": "s",
              "contract_change_requests": [{"contract": ".devpipeline/architecture.json", "change": "declare e2e check", "reason": "not owned"}]})
        res, _ = f.gate()
        issue = next(read_json(p) for p in (f.rd / "issues").glob("ISSUE-*.json") if read_json(p)["category"] == "contract")
        self.assertTrue(issue["blocking"])
        self.assertEqual(issue["assigned_agent"], "software-architect")
        self.assertIn(issue["id"], res["blocking_issue_ids"])
        tasks = read_json(f.rd / "tasks.json")["tasks"]
        tasks.append(task("TASK-910-R1", agent="software-architect", owned=[], status="COMPLETED", kind="contract", issue_ids=[issue["id"]]))
        write(f.rd / "tasks.json", {"tasks": tasks})
        res, _ = f.gate()
        self.assertNotIn(issue["id"], res["blocking_issue_ids"])
        self.assertEqual(read_json(f.rd / "issues" / f"{issue['id']}.json")["status"], "RESOLVED")


class WindowsTests(unittest.TestCase):
    def test_pid_probe_does_not_kill(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            self.assertTrue(dp.pid_alive(child.pid))
            self.assertIsNone(child.poll(), "probing must not terminate the process (os.kill(pid, 0) does on Windows)")
        finally:
            child.kill()
            child.wait()
        self.assertFalse(dp.pid_alive(child.pid))

    def test_mcp_server_is_started_through_node_not_npx(self):
        mcp = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["playwright"]
        self.assertEqual(mcp["command"], "node")
        self.assertTrue((ROOT / "scripts" / Path(mcp["args"][0]).name).is_file())


class IstanbulReelsResumeTests(unittest.TestCase):
    """istanbul-reels: Next.js rewrites next-env.d.ts on every build/dev (INC-017)."""

    def test_checks_restore_files_their_tools_rewrite(self):
        f = GateFixture()
        write(f.wt / "next-env.d.ts", "// build\n")
        write(f.wt / "gen.js", "require('fs').writeFileSync('next-env.d.ts', '// dev\\n'); require('fs').writeFileSync('new-gen.txt', 'x')\n")
        dp.wm.commit_integration(f.wt, "gen")
        pkg = read_json(f.wt / "package.json")
        pkg["scripts"]["build"] = "node gen.js"
        write(f.wt / "package.json", pkg)
        dp.wm.commit_integration(f.wt, "build script")
        f.checks(); f.qa(); f.final()
        self.assertEqual((f.wt / "next-env.d.ts").read_text(), "// build\n")
        self.assertFalse((f.wt / "new-gen.txt").exists())
        _, g = f.gate()
        self.assertEqual(g["unit_tests"]["status"], "PASS", "evidence is not stale after the build rewrote a file")

    def test_qa_commit_reverts_generated_files_instead_of_blocking(self):
        f = GateFixture()
        write(f.wt / "next-env.d.ts", "// dev\n")
        write(f.wt / "test" / "b.test.js", "require('node:test')('b', () => {})\n")
        out = dp.op_commit_integration(f.project, f.run, "qa", "qa")
        self.assertEqual(out["violations"], [])
        self.assertIn("next-env.d.ts", out["reverted"])
        self.assertFalse(sm.load_manifest(f.project, f.run).get("ownership_violations"))
        self.assertFalse(dp.gitutil.status_porcelain(f.wt), "test committed, generated file reverted")

    def test_failed_repair_whose_issues_are_resolved_does_not_block(self):
        f = GateFixture()
        f.checks(); f.qa(); f.final()
        write(f.rd / "issues" / "ISSUE-050.json", {"id": "ISSUE-050", "source_agent": "code-qa-engineer", "assigned_agent": "backend-engineer",
              "severity": "high", "category": "functional", "description": "x", "blocking": True, "status": "RESOLVED"})
        tasks = read_json(f.rd / "tasks.json")["tasks"]
        tasks.append(task("TASK-912-R1", status="FAILED", kind="repair", issue_ids=["ISSUE-050"]))
        write(f.rd / "tasks.json", {"tasks": tasks})
        _, g = f.gate()
        self.assertEqual(g["task_completion"]["status"], "PASS", g["task_completion"]["reason"])
        write(f.rd / "issues" / "ISSUE-050.json", {**read_json(f.rd / "issues" / "ISSUE-050.json"), "status": "OPEN"})
        _, g = f.gate()
        self.assertEqual(g["task_completion"]["status"], "FAIL")

    def test_executed_fake_model_eval_counts_as_evidence(self):
        f = GateFixture(features={"backend": True, "ai": True})
        f.checks(); f.qa(); f.final()
        log = write(f.rd / "logs" / "ai-eval-det.log", "5 passed")
        write(f.rd / "test-reports" / "ai_eval.json", {"run_id": f.run, "agent": "ai-evaluation-engineer", "kind": "ai_eval", "source_fingerprint": f.fps()["prod"],
              "status": "PASS", "issues": [], "summary": "s", "checks": [
                  {"name": "deterministic:topics-filter", "status": "PASS", "evidence_type": "mock", "exit_code": 0, "log_path": str(log)},
                  {"name": "live:grounding", "status": "NOT_RUN", "evidence_type": "executed", "exit_code": None, "reason": "no credentials"}]})
        _, g = f.gate()
        self.assertEqual(g["ai_evaluation"]["status"], "PASS", g["ai_evaluation"]["reason"])
        write(f.rd / "test-reports" / "ai_eval.json", {**read_json(f.rd / "test-reports" / "ai_eval.json"), "checks": [
            {"name": "judge", "status": "PASS", "evidence_type": "mock"}]})
        _, g = f.gate()
        self.assertNotEqual(g["ai_evaluation"]["status"], "PASS", "a mock result that was never run is still not evidence")


if __name__ == "__main__":
    unittest.main()
