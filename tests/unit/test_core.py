"""Schemas, configuration, DAG validation, agent selection, issues, contracts, redaction."""
import json
import unittest

from helpers import ROOT, requirements, task

from dpcore import config as cfgmod
from dpcore.dag import blocked_by_failure, find_cycle, path_owned, paths_overlap, ready_tasks, validate_tasks
from dpcore.issues import dedupe, normalize, route, stalled, update_history
from dpcore.jsonschema_lite import load_schema, unsupported_keywords, validate_named
from dpcore.selection import select_agents
from dpcore.util import DPError, canonical_json, fnv1a32, with_checksum
import redact_secrets
import validate_contracts as vc
import validate_config

REG = cfgmod.registry()["agents"]
ROLES = {k: v["role"] for k, v in REG.items()}


class SchemaTests(unittest.TestCase):
    def test_all_schema_keywords_supported(self):
        for f in sorted((ROOT / "schemas").glob("*.json")):
            self.assertEqual(unsupported_keywords(load_schema(f.name)), set(), f.name)

    def test_requirements_valid_and_invalid(self):
        self.assertEqual(validate_named(requirements(), "requirements.schema.json"), [])
        bad = requirements()
        bad["requirements"][0]["id"] = "R1"
        del bad["requirements"][0]["acceptance_criteria"][0]["verification_method"]
        errs = validate_named(bad, "requirements.schema.json")
        self.assertTrue(any("REQ-" in e for e in errs) and any("verification_method" in e for e in errs), errs)

    def test_cross_file_ref(self):
        res = {"run_id": "r", "task_id": "TASK-001", "agent": "backend-engineer", "status": "COMPLETED", "changed_files": [],
               "validation": [], "issues": [{"id": "BAD"}], "summary": "x"}
        self.assertTrue(any("issues[0]" in e for e in validate_named(res, "agent-result.schema.json")))

    def test_templates_validate(self):
        self.assertEqual(validate_named(json.loads((ROOT / "templates/TASKS.json").read_text()), "task.schema.json"), [])
        self.assertEqual(validate_named(json.loads((ROOT / "templates/project-config.json").read_text()), "project.schema.json"), [])


class ConfigTests(unittest.TestCase):
    def test_profiles_resolve(self):
        self.assertEqual(cfgmod.resolve("fast")["gates"]["code_review"], "disabled")
        s = cfgmod.resolve("strict")
        self.assertEqual(s["live_ai_evaluation"], "mandatory")
        self.assertEqual(s["model_routing"]["final-reviewer"], "opus")
        self.assertEqual(cfgmod.resolve("balanced")["model_routing"]["technical-writer"], "haiku")

    def test_overrides_validated(self):
        self.assertEqual(cfgmod.resolve("balanced", {"max_parallel_agents": 5})["max_parallel_agents"], 5)
        with self.assertRaises(DPError):
            cfgmod.resolve("balanced", {"max_parallel_agents": 0})
        with self.assertRaises(DPError):
            cfgmod.resolve("balanced", {"agents": {"final-reviewer": "disabled"}})
        with self.assertRaises(DPError):
            cfgmod.resolve("balanced", {"gates": {"nope": "optional"}})
        with self.assertRaises(DPError):
            cfgmod.resolve("turbo")

    def test_framework_configuration_valid(self):
        res = validate_config.run()
        self.assertTrue(res["ok"], res["errors"])


class DagTests(unittest.TestCase):
    def v(self, tasks):
        return validate_tasks(tasks, set(REG), ROLES)

    def test_valid_graph_and_order(self):
        r = self.v([task("TASK-001"), task("TASK-002", deps=["TASK-001"], owned=["web/"], agent="frontend-engineer")])
        self.assertTrue(r["valid"], r["errors"])
        self.assertEqual(r["order"], ["TASK-001", "TASK-002"])

    def test_duplicate_unknown_missing(self):
        r = self.v([task("TASK-001"), task("TASK-001"), task("TASK-002", agent="wizard", deps=["TASK-009"])])
        msg = " ".join(r["errors"])
        for needle in ("duplicate task id", "unknown agent", "missing dependency"):
            self.assertIn(needle, msg)

    def test_cycle_detected(self):
        r = self.v([task("TASK-001", deps=["TASK-003"]), task("TASK-002", deps=["TASK-001"], owned=["b/"]), task("TASK-003", deps=["TASK-002"], owned=["c/"])])
        self.assertFalse(r["valid"])
        self.assertTrue(any("cycle" in e for e in r["errors"]))
        self.assertIsNotNone(find_cycle([task("TASK-001", deps=["TASK-001"])]))

    def test_ownership_conflicts_only_for_independent_tasks(self):
        r = self.v([task("TASK-001", owned=["src/"]), task("TASK-002", owned=["src/api/"]), task("TASK-003", owned=["src/x.js"], deps=["TASK-001", "TASK-002"])])
        self.assertEqual(r["conflicts"], [["TASK-001", "TASK-002"]])
        self.assertTrue(r["valid"])

    def test_writing_task_needs_owned_paths_and_safe_paths(self):
        r = self.v([task("TASK-001", owned=[]), task("TASK-002", owned=["../etc/"]), task("TASK-003", owned=[".devpipeline/state.json"])])
        self.assertEqual(len(r["errors"]), 3, r["errors"])

    def test_path_semantics(self):
        self.assertTrue(paths_overlap("src/", "src/a.js"))
        self.assertFalse(paths_overlap("src/", "web/"))
        self.assertTrue(paths_overlap("**/*.test.ts", "src/a.test.ts"))
        self.assertTrue(path_owned("src/a/b.js", ["src/"]))
        self.assertFalse(path_owned("srcx/a.js", ["src/"]))
        self.assertTrue(path_owned("README.md", ["*.md"]))

    def test_ready_respects_deps_conflicts_and_limit(self):
        ts = [task("TASK-001", status="COMPLETED"), task("TASK-002", deps=["TASK-001"]), task("TASK-003"), task("TASK-004")]
        self.assertEqual(ready_tasks(ts, [["TASK-002", "TASK-003"]], 3), ["TASK-002", "TASK-004"])
        self.assertEqual(ready_tasks(ts, [], 1), ["TASK-002"])

    def test_failure_blocks_downstream(self):
        ts = [task("TASK-001", status="FAILED"), task("TASK-002", deps=["TASK-001"]), task("TASK-003", deps=["TASK-002"]), task("TASK-004")]
        self.assertEqual(blocked_by_failure(ts), ["TASK-002", "TASK-003"])


class SelectionTests(unittest.TestCase):
    def names(self, **kw):
        return [s["agent"] for s in select_agents(**kw)["selected"]]

    def test_ai_project_activates_ai_agents(self):
        n = self.names(features={"backend": True, "ai": True}, profile="balanced", mode="build")
        self.assertIn("ai-engineer", n)
        self.assertIn("ai-evaluation-engineer", n)

    def test_architect_cannot_drop_ai_evaluation(self):
        res = select_agents({"ai": True, "backend": True}, "balanced", "build", ["ai-engineer", "backend-engineer"], [],
                            {"ai-evaluation-engineer": "disabled"})
        self.assertIn("ai-evaluation-engineer", [s["agent"] for s in res["selected"]])
        self.assertTrue(any("cannot disable" in e for e in res["errors"]))

    def test_non_ai_backend(self):
        n = self.names(features={"backend": True}, profile="balanced", mode="build")
        for a in ("ai-engineer", "ai-evaluation-engineer", "frontend-engineer", "browser-qa-engineer", "ui-ux-designer"):
            self.assertNotIn(a, n)
        for a in ("backend-engineer", "integration-engineer", "code-qa-engineer", "security-engineer", "code-reviewer", "final-reviewer", "technical-writer"):
            self.assertIn(a, n)

    def test_fast_profile_and_strict_profile(self):
        self.assertNotIn("code-reviewer", self.names(features={"backend": True}, profile="fast", mode="build"))
        self.assertNotIn("security-engineer", self.names(features={"backend": True}, profile="fast", mode="build"))
        self.assertIn("security-engineer", self.names(features={"backend": True, "authentication": True}, profile="fast", mode="build"))
        self.assertIn("performance-engineer", self.names(features={"backend": True}, profile="strict", mode="build"))

    def test_web_ui_gets_browser_qa_and_designer(self):
        n = self.names(features={"frontend": True}, profile="balanced", mode="build")
        self.assertIn("browser-qa-engineer", n)
        self.assertIn("ui-ux-designer", n)

    def test_audit_has_no_writers(self):
        n = self.names(features={"backend": True, "ai": True}, profile="balanced", mode="audit")
        self.assertFalse(set(n) & {"backend-engineer", "ai-engineer", "product-analyst", "technical-writer", "integration-engineer"})
        self.assertIn("ai-evaluation-engineer", n)
        self.assertIn("code-qa-engineer", n)


class IssueTests(unittest.TestCase):
    def test_normalize_dedupe(self):
        a = normalize({"category": "functional", "description": "Login fails for user 12", "affected_files": ["./src/a.js"], "severity": "medium"}, "browser-qa-engineer")
        b = normalize({"category": "functional", "description": "Login fails for user 99", "affected_files": ["src/a.js"], "severity": "high"}, "code-qa-engineer")
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        d = dedupe([a, b])
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["severity"], "high")
        self.assertTrue(d[0]["blocking"])

    def test_security_high_always_blocking(self):
        self.assertTrue(normalize({"category": "security", "severity": "high", "description": "x", "blocking": False})["blocking"])

    def test_routing(self):
        tasks = [task("TASK-001", agent="frontend-engineer", owned=["web/"]), task("TASK-002", agent="backend-engineer", owned=["api/"])]
        sel = {"frontend-engineer", "backend-engineer", "integration-engineer", "technical-writer"}
        self.assertEqual(route({"category": "api", "affected_files": []}, tasks, sel), "backend-engineer")
        self.assertEqual(route({"category": "security", "affected_files": ["web/x.ts"]}, tasks, sel), "frontend-engineer")
        self.assertEqual(route({"category": "visual", "affected_files": []}, tasks, sel), "frontend-engineer")  # designer absent -> fallback
        self.assertEqual(route({"category": "documentation"}, tasks, sel), "technical-writer")
        self.assertEqual(route({"category": "integration"}, tasks, sel), "integration-engineer")

    def test_no_progress_detection(self):
        h = {}
        for it in range(3):
            update_history(h, ["fp"], it)
        self.assertEqual(stalled(h, ["fp"], 2, 2), ["fp"])
        self.assertEqual(stalled(h, ["fp"], 1, 2), [])
        self.assertEqual(stalled({"fp": [0, 2]}, ["fp"], 2, 2), [])


class ContractTests(unittest.TestCase):
    def test_null_and_invalid_results(self):
        self.assertFalse(vc.check_result(None)["valid"])
        res = {"run_id": "r", "task_id": "TASK-001", "agent": "backend-engineer", "status": "COMPLETED", "changed_files": [],
               "validation": [{"name": "unit", "status": "PASS", "exit_code": 1}], "issues": [], "summary": "s"}
        out = vc.check_result(res, "TASK-002", "frontend-engineer", "r")
        msg = " ".join(out["errors"])
        self.assertIn("task_id mismatch", msg)
        self.assertIn("agent mismatch", msg)
        self.assertIn("exit code 1", msg)
        self.assertFalse(vc.check_result({"not": "a result"})["valid"])

    def test_requirements_rules(self):
        r = requirements()
        r["requirements"].append(dict(r["requirements"][0]))
        self.assertTrue(any("duplicate" in e for e in vc.check_requirements(r)["errors"]))

    def test_stack_preference_enforced(self):
        req = requirements()
        req["stack_preferences"] = {"backend": "FastAPI"}
        arch = {"project_type": "api", "stack": {"backend": "Express"}, "features": {}, "components": [], "ownership": [],
                "required_agents": [], "test_strategy": {}, "adrs": []}
        self.assertTrue(any("FastAPI" in e for e in vc.check_architecture(arch, req)["errors"]))
        arch["stack_change_justification"] = "user approved in ADR-002"
        self.assertTrue(vc.check_architecture(arch, req)["valid"])

    def test_uncovered_must_requirement(self):
        out = vc.check_tasks({"tasks": [task("TASK-001")]}, requirements())
        self.assertTrue(any("REQ-001" in e for e in out["errors"]))


class RedactionTests(unittest.TestCase):
    def test_redacts_common_secrets(self):
        text = "key sk-ant-api03-" + "a" * 40 + " aws AKIAABCDEFGHIJKLMNOP\nAPI_KEY=Zx81kq0PpLm3Rt7vWy2b\npostgres://u:hunter2pass@db/x"
        out = redact_secrets.redact(text)
        self.assertNotIn("hunter2pass", out)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", out)
        self.assertNotIn("Zx81kq0PpLm3Rt7vWy2b", out)
        self.assertIn("[REDACTED:", out)

    def test_placeholders_not_flagged(self):
        self.assertEqual(redact_secrets.find("API_KEY=changeme\nTOKEN=${TOKEN}\nPASSWORD=<your-password>"), [])


class ChecksumTests(unittest.TestCase):
    def test_checksum_is_canonical_and_matches_js(self):
        import subprocess
        payload = {"b": [1, "ğüş é", {"z": None, "a": True}], "a": 1.0}
        py = with_checksum(payload)["checksum"]
        engine = (ROOT / "scripts/workflow_src/engine.js").read_text()
        start = engine.index("function utf8Bytes")
        end = engine.index("function q(s)")
        js = engine[start:end] + "\nconsole.log(fnv1a32(canonical(" + json.dumps({"b": [1, "ğüş é", {"z": None, "a": True}], "a": 1}) + ")))"
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, py)
        self.assertEqual(fnv1a32(canonical_json({"x": 1})), fnv1a32('{"x":1}'))


class FixtureTests(unittest.TestCase):
    """The examples from the specification validate against the schemas (and the invalid one does not)."""

    def load(self, name):
        return json.loads((ROOT / "tests" / "fixtures" / name).read_text())

    def test_fixtures(self):
        self.assertTrue(vc.check_result(self.load("agent-result.valid.json"), "TASK-005", "frontend-engineer", "run-001")["valid"])
        bad = vc.check_result(self.load("agent-result.invalid.json"))
        self.assertFalse(bad["valid"])
        self.assertGreaterEqual(len(bad["errors"]), 4)
        self.assertEqual(validate_named(self.load("issue.valid.json"), "issue.schema.json"), [])
        self.assertEqual(validate_named(self.load("final-review.example.json"), "review.schema.json"), [])


if __name__ == "__main__":
    unittest.main()
