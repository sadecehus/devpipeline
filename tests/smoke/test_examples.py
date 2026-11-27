"""Example projects: agent activation and gate applicability (deterministic, no model usage)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from dpcore import config as cfgmod  # noqa: E402
from dpcore.selection import merge_features, select_agents  # noqa: E402
import quality_gate  # noqa: E402


class FakeCtx:
    def __init__(self, features, profile, selected):
        self.features, self.mode, self.selected = features, "build", set(selected)
        self.cfg = cfgmod.resolve(profile)
        self.detected_kinds = {"install", "build", "lint"}
        self.requirements = None

    def impl_count(self):
        reg = cfgmod.registry()
        return sum(1 for a in self.selected if reg["agents"][a]["role"] == "implementation")


class ExampleTests(unittest.TestCase):
    def check(self, name):
        exp = json.loads((ROOT / "examples" / name / "expected.json").read_text())
        self.assertTrue((ROOT / "examples" / name / "request.md").read_text().strip())
        feats = merge_features(exp["features"])
        task_agents = [a for a in exp["expected_agents"] if cfgmod.registry()["agents"][a]["role"] == "implementation"]
        sel = [s["agent"] for s in select_agents(feats, exp["profile"], "build", task_agents, task_agents)["selected"]]
        for a in exp["expected_agents"]:
            self.assertIn(a, sel, f"{name}: {a} should be active")
        for a in exp["excluded_agents"]:
            self.assertNotIn(a, sel, f"{name}: {a} must not be active")
        ctx = FakeCtx(feats, exp["profile"], sel)
        gates = {g["id"]: g for g in cfgmod.gates_config()["gates"]}
        for gid in exp.get("mandatory_gates", []):
            ok, why = quality_gate.applies(gates[gid], ctx)
            level, _ = quality_gate.level_for(gates[gid], ctx)
            self.assertTrue(ok and level == "mandatory", f"{name}: {gid} should be mandatory ({why}, {level})")
        for gid in exp.get("not_applicable_gates", []):
            self.assertFalse(quality_gate.applies(gates[gid], ctx)[0], f"{name}: {gid} should be N/A")

    def test_fullstack_todo(self):
        self.check("fullstack-todo")

    def test_backend_api(self):
        self.check("backend-api")

    def test_ai_rag_assistant(self):
        self.check("ai-rag-assistant")


if __name__ == "__main__":
    unittest.main()
