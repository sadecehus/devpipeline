"""State machine, persistence, checkpoints, resume, stale evidence and the deterministic quality gate."""
import json
import threading
import unittest
from pathlib import Path

from helpers import git, node_project, requirements, task, tmpdir, write

import devpipeline as dp
import quality_gate
import run_checks
import state_manager as sm
from dpcore import gitutil
from dpcore.util import DPError, read_json


def new_run(mode="build", profile="balanced", overrides=None):
    project = tmpdir() / "p"
    project.mkdir()
    sm.ensure_project(project, created_by_devpipeline=True)
    m = sm.create_run(project, mode, "request", profile, overrides or {}, "new")
    return project, m["run_id"]


def walk(project, run, *phases):
    for p in phases:
        sm.transition(project, run, p, "test")


class StateTests(unittest.TestCase):
    def test_legal_and_illegal_transitions(self):
        project, run = new_run()
        walk(project, run, "ANALYZING", "PLANNING", "DEVELOPING")
        with self.assertRaises(DPError):
            sm.transition(project, run, "ANALYZING")        # backwards
        with self.assertRaises(DPError):
            sm.transition(project, run, "COMPLETED")        # not from DEVELOPING
        walk(project, run, "CANCELLED")
        with self.assertRaises(DPError):
            sm.transition(project, run, "PLANNING")         # terminal

    def test_completed_requires_passing_gate(self):
        project, run = new_run()
        walk(project, run, "ANALYZING", "PLANNING", "DEVELOPING", "INTEGRATING", "TESTING", "REVIEWING")
        with self.assertRaisesRegex(DPError, "no quality gate"):
            sm.transition(project, run, "COMPLETED")

    def test_checkpoints_and_events_persist(self):
        project, run = new_run()
        walk(project, run, "ANALYZING", "PLANNING")
        m = sm.load_manifest(project, run)
        self.assertEqual(len(m["checkpoints"]), 2)
        cp = read_json(project / ".devpipeline/checkpoints" / run / m["checkpoints"][-1])
        self.assertEqual(cp["manifest"]["phase"], "PLANNING")
        events = (sm.run_dir(project, run) / "events.jsonl").read_text().splitlines()
        self.assertTrue(all(json.loads(l)["run_id"] == run for l in events))
        self.assertIn("RUN_STARTED", events[0])

    def test_concurrent_writers_do_not_corrupt(self):
        project, run = new_run()
        def bump():
            for _ in range(20):
                with sm.mutate(project, run) as m:
                    m["counters"]["agent_calls"] += 1
        threads = [threading.Thread(target=bump) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sm.load_manifest(project, run)["counters"]["agent_calls"], 100)

    def test_invalid_manifest_never_persisted(self):
        project, run = new_run()
        with self.assertRaises(DPError):
            with sm.mutate(project, run) as m:
                m["phase"] = "PARTYING"
        self.assertEqual(sm.load_manifest(project, run)["phase"], "INITIALIZING")

    def test_resume_resets_interrupted_and_checks_identity(self):
        project, run = new_run()
        walk(project, run, "ANALYZING", "PLANNING", "DEVELOPING")
        write(sm.run_dir(project, run) / "tasks.json", {"tasks": [task("TASK-001", status="RUNNING"), task("TASK-002", status="COMPLETED", owned=["b/"])]})
        with sm.mutate(project, run) as m:
            m["tasks"] = {"TASK-001": {"status": "RUNNING", "attempt": 1}, "TASK-002": {"status": "COMPLETED", "attempt": 1}}
        res = sm.resume_check(project, run)
        self.assertTrue(res["ok"])
        self.assertEqual(res["reset_tasks"], ["TASK-001"])
        self.assertEqual(read_json(sm.run_dir(project, run) / "tasks.json")["tasks"][1]["status"], "COMPLETED")
        cfg = read_json(project / ".devpipeline/config.json")
        cfg["project_id"] = "ffffffff-0000-0000-0000-000000000000"
        write(project / ".devpipeline/config.json", cfg)
        self.assertFalse(sm.resume_check(project, run)["ok"])

    def test_existing_repo_uses_info_exclude(self):
        project = tmpdir() / "existing"
        project.mkdir()
        git(project, "init", "-q")
        sm.ensure_project(project, created_by_devpipeline=False)
        self.assertFalse((project / ".gitignore").exists())
        self.assertIn(".devpipeline/", (project / ".git/info/exclude").read_text())


class GateFixture:
    """A build run with an integration worktree containing a node project and controllable evidence."""

    def __init__(self, test_body="test('ok', () => assert.ok(true))\n", features=None, profile="balanced", req=None):
        self.project, self.run = new_run(profile=profile)
        write(self.project / ".devpipeline/requirements.json", req or requirements())
        info = dp.wm.setup_integration(self.project, self.run, "new")
        self.wt = Path(info["integration_worktree"])
        node_project(self.wt, test_body)
        write(self.wt / "README.md", "\n".join(["# P", "## Install", "npm ci", "## Run", "npm run build", "## Test", "npm test"] + [f"line {i}" for i in range(25)]))
        dp.wm.commit_integration(self.wt, "code")
        with sm.mutate(self.project, self.run) as m:
            m["source"].update(info)
            m["isolation"] = "worktree"
            m["features"] = features or {"backend": True}
            m["selected_agents"] = [{"agent": a, "reason": "t"} for a in ("backend-engineer", "code-qa-engineer", "final-reviewer")]
            m["plan_validation"] = {"valid": True}
        write(sm.run_dir(self.project, self.run) / "tasks.json", {"tasks": [task("TASK-001", status="COMPLETED", requirements=["REQ-001"])]})
        self.rd = sm.run_dir(self.project, self.run)

    def fps(self):
        return sm.fingerprints(self.project, sm.load_manifest(self.project, self.run))

    def checks(self):
        return run_checks.run(self.project, self.run, "t")

    def qa(self, status="PASS", fp=None, with_log=True, kind="code_qa", agent="code-qa-engineer"):
        log = write(self.rd / "logs/qa.log", "ok") if with_log else None
        write(self.rd / f"test-reports/{kind}.json", {"run_id": self.run, "agent": agent, "kind": kind, "source_fingerprint": fp or self.fps()["prod"],
              "status": status, "checks": [{"name": "c", "status": status, "evidence_type": "executed", "exit_code": 0 if status == "PASS" else 1,
                                             **({"log_path": str(log)} if log else {})}], "issues": [], "summary": "s"})

    def final(self, approved=True, fp=None, matrix_status="VERIFIED"):
        write(self.rd / "reviews/final_review.json", {"kind": "final_review", "agent": "final-reviewer", "approved": approved,
              "status": "APPROVED" if approved else "REPAIR_REQUIRED", "source_fingerprint": fp or self.fps()["code"],
              "acceptance_matrix": [{"ac_id": "AC-001", "requirement_id": "REQ-001", "status": matrix_status}],
              "blocking_issues": [], "non_blocking_issues": [], "summary": "s"})

    def gate(self):
        res = quality_gate.evaluate(self.project, self.run)
        full = read_json(self.rd / res["gate_file"])
        return res, {g["id"]: g for g in full["gates"]}


class GateTests(unittest.TestCase):
    def test_all_evidence_passes(self):
        f = GateFixture()
        f.checks(); f.qa(); f.final()
        res, g = f.gate()
        self.assertTrue(res["passed"], {k: (v["status"], v.get("reason")) for k, v in g.items() if v["mandatory"] and v["status"] != "PASS"})
        self.assertEqual(g["typecheck"]["status"], "NOT_APPLICABLE")
        walk(f.project, f.run, "ANALYZING", "PLANNING", "DEVELOPING", "INTEGRATING", "TESTING", "REVIEWING", "COMPLETED")

    def test_failed_tests_fail_gate_and_create_issue(self):
        f = GateFixture("test('bad', () => assert.strictEqual(1, 2))\n")
        f.checks(); f.qa(); f.final()
        res, g = f.gate()
        self.assertFalse(res["passed"])
        self.assertEqual(g["unit_tests"]["status"], "FAIL")
        self.assertTrue(res["blocking_issue_ids"])

    def test_skipped_tests_block(self):
        f = GateFixture("test('ok', () => {})\ntest.skip('later', () => {})\n")
        f.checks(); f.qa(); f.final()
        res, g = f.gate()
        self.assertEqual(g["unit_tests"]["status"], "SKIPPED")
        self.assertFalse(res["passed"])

    def test_zero_tests_is_not_a_pass(self):
        f = GateFixture("")
        (f.wt / "test/a.test.js").unlink()
        write(f.wt / "test/.keep", "")
        f.checks(); f.qa(); f.final()
        _, g = f.gate()
        self.assertNotEqual(g["unit_tests"]["status"], "PASS")

    def test_missing_and_stale_evidence(self):
        f = GateFixture()
        res, g = f.gate()
        self.assertEqual(g["unit_tests"]["status"], "NOT_RUN")
        self.assertEqual(g["code_qa"]["status"], "NOT_RUN")
        self.assertEqual(g["final_review"]["status"], "NOT_RUN")
        f.checks(); f.qa(); f.final()
        write(f.wt / "index.js", "module.exports = 2\n")    # code changes after evidence
        res, g = f.gate()
        self.assertFalse(res["passed"])
        for gid in ("unit_tests", "code_qa", "final_review"):
            self.assertEqual(g[gid]["status"], "NOT_RUN", gid)
        stale = sm._mark_stale_reports(f.project, f.run)
        self.assertIn("code_qa.json", stale)

    def test_qa_tests_do_not_invalidate_qa_reports_but_do_invalidate_checks(self):
        f = GateFixture()
        f.checks(); f.qa(); f.final()
        write(f.wt / "test/extra.test.js", "require('node:test')('x', () => {})\n")
        _, g = f.gate()
        self.assertEqual(g["code_qa"]["status"], "PASS")          # production fingerprint unchanged
        self.assertEqual(g["unit_tests"]["status"], "NOT_RUN")    # checks must re-run

    def test_pass_claim_without_log_is_error(self):
        f = GateFixture()
        f.checks(); f.qa(with_log=False); f.final()
        _, g = f.gate()
        self.assertEqual(g["code_qa"]["status"], "ERROR")

    def test_llm_approval_cannot_override_failed_check(self):
        f = GateFixture("test('bad', () => assert.strictEqual(1, 2))\n")
        f.checks(); f.qa(); f.final(approved=True)
        res, g = f.gate()
        self.assertEqual(g["final_review"]["status"], "PASS")
        self.assertFalse(res["passed"])
        with self.assertRaises(DPError):
            walk(f.project, f.run, "ANALYZING", "PLANNING", "DEVELOPING", "INTEGRATING", "TESTING", "REVIEWING", "COMPLETED")

    def test_unverified_acceptance_criterion_fails_coverage(self):
        f = GateFixture()
        f.checks(); f.qa(); f.final(matrix_status="IMPLEMENTED_UNVERIFIED")
        res, g = f.gate()
        self.assertEqual(g["requirements_coverage"]["status"], "FAIL")
        self.assertFalse(res["passed"])

    def test_requirement_forces_disabled_gate_mandatory(self):
        req = requirements(extra_ac={"id": "AC-002", "description": "p95 latency below 300 ms", "verification_method": "performance"})
        f = GateFixture(req=req)
        _, g = f.gate()
        self.assertEqual(g["performance"]["level"], "mandatory")

    def test_not_applicable_is_rule_based(self):
        f = GateFixture(features={"backend": True})
        _, g = f.gate()
        self.assertEqual(g["e2e_tests"]["status"], "NOT_APPLICABLE")
        f2 = GateFixture(features={"frontend": True})
        _, g2 = f2.gate()
        self.assertTrue(g2["e2e_tests"]["mandatory"])
        self.assertEqual(g2["e2e_tests"]["status"], "NOT_RUN")

    def test_repair_limits_and_no_progress(self):
        f = GateFixture("test('bad', () => assert.strictEqual(1, 2))\n")
        f.checks(); f.qa(); f.final()
        f.gate()
        out = dp.op_plan_repair(f.project, f.run, 6)
        self.assertTrue(out["stop"])
        self.assertIn("budget exhausted", out["reason"])
        plan = dp.op_plan_repair(f.project, f.run, 1)
        self.assertFalse(plan["stop"])
        self.assertEqual(plan["repair_tasks"][0]["agent"], "backend-engineer")
        for it in (1, 2):
            with sm.mutate(f.project, f.run) as m:
                m["counters"]["repair_iterations"] = it
            f.checks(); f.qa(); f.final(); f.gate()
        stop = dp.op_plan_repair(f.project, f.run, 3)
        self.assertTrue(stop["stop"])
        self.assertIn("no progress", stop["reason"])


class RunChecksTests(unittest.TestCase):
    def test_discovery_uses_real_scripts_only(self):
        d = tmpdir()
        node_project(d, scripts={"test": "node --test test/", "lint": "node --check index.js"})
        kinds = {c["kind"]: c for c in run_checks.discover(d)}
        self.assertEqual(kinds["unit"]["command"], "npm run test")
        self.assertNotIn("build", kinds)
        self.assertIn("secret-scan", [c["name"] for c in run_checks.discover(d)])

    def test_missing_lockfile_fails_install(self):
        d = tmpdir()
        write(d / "package.json", {"name": "x", "scripts": {"test": "node -e 1"}})
        inst = [c for c in run_checks.discover(d) if c["kind"] == "install"][0]
        self.assertEqual(inst["force_status"], "FAIL")

    def test_command_validation_and_env(self):
        self.assertIn("metacharacters", run_checks._validate_command("npm test; rm -rf /"))
        self.assertIn("not in the allowed list", run_checks._validate_command("curl http://x"))
        import os
        os.environ["SOME_API_KEY"] = "x"
        self.assertNotIn("SOME_API_KEY", run_checks._safe_env())


class VerifierRegressionTests(unittest.TestCase):
    def test_agent_cannot_opt_out_of_its_checks(self):
        f = GateFixture()
        f.checks(); f.final()
        write(f.rd / "test-reports/code_qa.json", {"run_id": f.run, "agent": "code-qa-engineer", "kind": "code_qa", "source_fingerprint": f.fps()["prod"],
              "status": "PASS", "checks": [{"name": "a", "status": "SKIPPED", "mandatory": False}, {"name": "b", "status": "NOT_RUN", "mandatory": False}],
              "issues": [], "summary": "s"})
        _, g = f.gate()
        self.assertNotEqual(g["code_qa"]["status"], "PASS")

    def test_unparsed_test_count_is_not_a_pass(self):
        from helpers import node_project
        f = GateFixture()
        node_project(f.wt, scripts={"test": "node -e 1", "build": "node --check index.js"})
        f.checks(); f.qa(); f.final()
        _, g = f.gate()
        self.assertEqual(g["unit_tests"]["status"], "NOT_RUN")

    def test_forged_check_report_is_ignored(self):
        f = GateFixture("test('bad', () => assert.strictEqual(1, 2))\n")
        f.checks()
        forged = read_json(f.rd / "test-reports/checks-t.json")
        for c in forged["checks"]:
            c["status"], c["exit_code"] = "PASS", 0
        forged["status"] = "PASS"
        write(f.rd / "test-reports/checks-zz-forged.json", forged)   # newer, but not recorded by run_checks
        f.qa(); f.final()
        _, g = f.gate()
        self.assertEqual(g["unit_tests"]["status"], "FAIL")

    def test_qa_report_must_come_from_owning_agent(self):
        f = GateFixture()
        f.checks(); f.final()
        f.qa(agent="backend-engineer")
        _, g = f.gate()
        self.assertEqual(g["code_qa"]["status"], "NOT_RUN")

    def test_non_executed_pass_is_not_evidence(self):
        f = GateFixture()
        f.checks(); f.final()
        write(f.rd / "test-reports/code_qa.json", {"run_id": f.run, "agent": "code-qa-engineer", "kind": "code_qa", "source_fingerprint": f.fps()["prod"],
              "status": "PASS", "checks": [{"name": "a", "status": "PASS", "evidence_type": "llm_judgment"}], "issues": [], "summary": "s"})
        _, g = f.gate()
        self.assertEqual(g["code_qa"]["status"], "NOT_RUN")

    def test_second_edit_of_dirty_user_file_is_detected(self):
        from helpers import git
        import worktree_manager as wm
        d = tmpdir() / "u"
        d.mkdir()
        git(d, "init", "-q")
        write(d / "a.txt", "1")
        git(d, "add", "-A"); git(d, "commit", "-qm", "x")
        write(d / "a.txt", "2")
        before = wm.checkout_state(d)
        write(d / "a.txt", "3")
        self.assertNotEqual(before["hashes"], wm.checkout_state(d)["hashes"])
        write(d / ".env", "SECRET=1")
        self.assertFalse(any(".env" in l for l in wm.checkout_state(d)["status"]))

    def test_op_key_makes_operations_idempotent(self):
        import json as _json, subprocess, sys as _sys
        from helpers import ROOT
        project, run = new_run()
        cmd = [_sys.executable, str(ROOT / "scripts/devpipeline.py"), "transition", "--project", str(project), "--run", run,
               "--to", "ANALYZING", "--op-key", "inv1-1-transition"]
        a = subprocess.run(cmd, capture_output=True, text=True).stdout
        sm.transition(project, run, "PLANNING", "moved on")
        b = subprocess.run(cmd, capture_output=True, text=True).stdout   # retry of the same logical op
        self.assertEqual(a, b)
        self.assertEqual(sm.load_manifest(project, run)["phase"], "PLANNING", "cached retry did not re-execute")
        self.assertIn("checksum", _json.loads(b))

    def test_output_is_raw_utf8_and_checksum_transport_independent(self):
        from dpcore.util import emit_line, canonical_json, fnv1a32
        import json as _json
        line = emit_line({"title": "Kız Kulesi"})
        self.assertIn("Kız", line)
        d = _json.loads(line)
        cs = d.pop("checksum")
        self.assertEqual(cs, fnv1a32(canonical_json(d)))

    def test_protocol_handshake(self):
        import json as _json, subprocess, sys as _sys
        from helpers import ROOT
        project, run = new_run()
        base = [_sys.executable, str(ROOT / "scripts/devpipeline.py"), "context", "--project", str(project), "--run", run]
        bad = _json.loads(subprocess.run(base + ["--protocol", "1"], capture_output=True, text=True).stdout)
        self.assertTrue(bad["protocol_mismatch"])
        self.assertFalse(bad["ok"])
        self.assertEqual(sm.load_manifest(project, run)["phase"], "INITIALIZING", "mismatch changes nothing")
        import devpipeline as dpm
        ok = _json.loads(subprocess.run(base + ["--protocol", str(dpm.PROTOCOL)], capture_output=True, text=True).stdout)
        self.assertTrue(ok["ok"])

    def test_result_normalisation_keeps_real_errors(self):
        import devpipeline as dpm
        d = tmpdir()
        f = write(d / "r.json", {"status": "COMPLETED", "artifacts": ["a.md"], "validation": [{"name": "t", "status": "PASS", "exit_code": 0, "evidence": "l.log"}], "summary": "s"})
        res = dpm.normalize_result(f, "run-1", "TASK-001", "backend-engineer")
        import validate_contracts as vc
        self.assertTrue(vc.check_result(res, "TASK-001", "backend-engineer", "run-1")["valid"], vc.check_result(res)["errors"])
        g = write(d / "g.json", {"task_id": "TASK-009", "status": "COMPLETED", "summary": "s"})
        self.assertFalse(vc.check_result(dpm.normalize_result(g, "run-1", "TASK-001", "backend-engineer"), "TASK-001")["valid"])

    def test_normalisation_null_evidence_and_string_issues(self):
        import devpipeline as dpm
        import validate_contracts as vc
        f = write(tmpdir() / "r.json", {"status": "COMPLETED", "changed_files": [], "summary": "s",
                                         "validation": [{"name": "e2e", "status": "FAIL", "exit_code": 1, "evidence_path": None}],
                                         "issues": ["publish spec failed on mobile", "a11y timeout"]})
        res = dpm.normalize_result(f, "run-1", "TASK-013", "frontend-engineer")
        self.assertTrue(vc.check_result(res, "TASK-013", "frontend-engineer", "run-1")["valid"], vc.check_result(res)["errors"])
        self.assertFalse(res["issues"][0]["blocking"])

    def test_declared_check_dedupes_identical_discovered_check(self):
        from helpers import node_project
        d = tmpdir()
        node_project(d)
        checks = run_checks.discover(d, {"checks": [{"name": "unit", "kind": "unit", "command": "npm run test"}]})
        self.assertEqual([c["name"] for c in checks if c["kind"] == "unit"], ["unit"])

    def test_long_checks_run_as_detached_job_with_bounded_waits(self):
        import devpipeline as dpm
        f = GateFixture()
        write(f.wt / "slow.js", "setTimeout(() => {}, 9000)\n")
        pkg = read_json(f.wt / "package.json")
        pkg["scripts"]["test:integration"] = "node slow.js"
        write(f.wt / "package.json", pkg)
        start = dpm.op_start_checks(f.project, f.run, "slow", None, False, "inv-slow")
        self.assertTrue(start["pending"])
        self.assertEqual(dpm.op_start_checks(f.project, f.run, "slow", None, False, "inv-slow")["job"], "inv-slow")  # idempotent
        first = dpm.op_wait_checks(f.project, f.run, "inv-slow", 5)
        self.assertTrue(first["pending"], "a slice returns before the relay limit")
        res = first
        for _ in range(10):
            res = dpm.op_wait_checks(f.project, f.run, "inv-slow", 30)
            if not res["pending"]:
                break
        self.assertFalse(res["pending"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["report"], "test-reports/checks-slow.json")
        self.assertIn("test-reports/checks-slow.json", sm.load_manifest(f.project, f.run)["check_reports"])

    def test_ai_engineer_task_forces_ai_evaluation(self):
        from dpcore.selection import select_agents
        sel = [s["agent"] for s in select_agents({"backend": True}, "balanced", "build", ["ai-engineer"], ["ai-engineer"])["selected"]]
        self.assertIn("ai-evaluation-engineer", sel)

    def test_runtime_limit(self):
        project, run = new_run(overrides={"max_runtime_minutes": 1})
        with sm.mutate(project, run) as m:
            m["started_at"] = "2020-01-01T00:00:00Z"
        with self.assertRaisesRegex(DPError, "max_runtime_minutes"):
            sm.transition(project, run, "ANALYZING")
        sm.transition(project, run, "BUDGET_EXHAUSTED", "limit")

    def test_request_is_redacted(self):
        project = tmpdir() / "p"
        project.mkdir()
        m = sm.create_run(project, "build", "use key sk-ant-api03-" + "x" * 40, "balanced", {}, "new")
        self.assertNotIn("sk-ant-api03", m["original_request"])


if __name__ == "__main__":
    unittest.main()
