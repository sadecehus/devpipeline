// Mocked orchestration tests (deterministic fake agents + real helpers).
// Run: node --test tests/integration/
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, readdirSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { runWorkflow, py, Killed } from './harness.mjs'
import { team } from './fakes.mjs'

function newProject(name = 'proj') { return join(mkdtempSync(join(tmpdir(), 'dp-it-')), name) }
function prepare(dir, mode = 'build', extra = []) {
  const out = py(['prepare', '--project', dir, '--mode', mode, '--request', 'Build a small calc service; Türkçe destekli', ...extra])
  assert.equal(out.ok, true, out.error)
  return out
}
function manifest(dir, run) { return JSON.parse(readFileSync(join(dir, '.devpipeline', 'runs', run, 'manifest.json'), 'utf8')) }
function tasks(dir, run) { return JSON.parse(readFileSync(join(dir, '.devpipeline', 'runs', run, 'tasks.json'), 'utf8')).tasks }
function lastGate(dir, run) {
  const m = manifest(dir, run)
  return JSON.parse(readFileSync(join(dir, '.devpipeline', 'runs', run, m.gate_results.at(-1).path), 'utf8'))
}
const gateStatus = (g, id) => g.gates.find(x => x.id === id).status
const selected = (dir, run) => manifest(dir, run).selected_agents.map(a => a.agent)

test('1. all-pass workflow reaches COMPLETED (and the workflow file is runtime-compliant)', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes, counts } = team({})
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.equal(out.meta.name, 'pipeline-build')
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
  const g = lastGate(dir, prep.run_id)
  assert.equal(g.passed, true)
  for (const x of g.gates) if (x.mandatory) assert.ok(['PASS', 'NOT_APPLICABLE'].includes(x.status), `${x.id}=${x.status}`)
  assert.equal(counts.writer, 1)
  assert.ok(existsSync(join(dir, 'README.md')), 'new project main checkout fast-forwarded')
  assert.ok(existsSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'final-report.md')))
  const report = readFileSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'final-report.md'), 'utf8')
  assert.match(report, /\*\*Final status:\*\* \*\*COMPLETED\*\*/)
  const events = readFileSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'events.jsonl'), 'utf8')
  for (const e of ['RUN_STARTED', 'AGENT_SELECTED', 'TASK_STARTED', 'TASK_COMPLETED', 'QUALITY_GATE_PASSED', 'RUN_COMPLETED', 'CHECKPOINT_CREATED']) assert.match(events, new RegExp(e))
})

test('2+3. a failing test is routed to the owning engineer and the repair triggers regression testing', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes, counts } = team({ bugFirstAttempt: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
  const repair = tasks(dir, prep.run_id).filter(t => t.kind === 'repair')
  assert.equal(repair.length, 1)
  assert.equal(repair[0].agent, 'backend-engineer')
  assert.ok(repair[0].issue_ids.length >= 1)
  const reports = readdirSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'test-reports'))
  assert.ok(reports.includes('checks-repair-1-post.json'), 'regression checks ran after repair')
  assert.equal(counts['code-qa'], 2, 'QA re-ran against the repaired revision')
  const issue = JSON.parse(readFileSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'issues', repair[0].issue_ids[0] + '.json'), 'utf8'))
  assert.equal(issue.status, 'RESOLVED')
})

test('4. a skipped mandatory test blocks completion', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({ skipTest: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.notEqual(out.result.final_status, 'COMPLETED')
  assert.equal(gateStatus(lastGate(dir, prep.run_id), 'unit_tests'), 'SKIPPED')
})

test('5. a missing agent result blocks completion (bounded retries)', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({ nullResult: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.equal(out.result.final_status, 'NEEDS_HUMAN_REVIEW')
  const t = tasks(dir, prep.run_id).find(x => x.id === 'TASK-001')
  assert.equal(t.status, 'FAILED')
  assert.equal(t.attempt, 3, 'initial attempt + max_task_retries (2)')
  assert.match(t.failure_reason, /missing agent result|returned null/)
})

test('6. a repeatedly unresolved issue terminates safely (no-progress detection)', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({ neverFix: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.equal(out.result.final_status, 'NEEDS_HUMAN_REVIEW')
  assert.match(out.result.reason, /no progress/)
  const m = manifest(dir, prep.run_id)
  assert.ok(m.counters.repair_iterations <= 3, `iterations ${m.counters.repair_iterations}`)
})

test('7. a failed security check blocks completion', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({ securityCritical: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.notEqual(out.result.final_status, 'COMPLETED')
  const g = lastGate(dir, prep.run_id)
  assert.equal(gateStatus(g, 'security_review'), 'FAIL')
})

test('8. an AI project activates AI engineering and AI evaluation; live eval NOT_RUN is reported honestly', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes, counts } = team({ ai: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  const sel = selected(dir, prep.run_id)
  assert.ok(sel.includes('ai-engineer') && sel.includes('ai-evaluation-engineer'))
  assert.equal(counts['ai-eval'] >= 1, true)
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
  const g = lastGate(dir, prep.run_id)
  assert.equal(gateStatus(g, 'ai_evaluation'), 'PASS')
  assert.match(g.gates.find(x => x.id === 'ai_evaluation').reason, /live evaluation NOT_RUN/)
})

test('8b. strict profile: unexecuted mandatory live AI evaluation blocks completion', async () => {
  const dir = newProject()
  const prep = prepare(dir, 'build', ['--profile', 'strict', '--overrides-json', '{"require_plan_approval": false}'])
  const { fakes } = team({ ai: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.notEqual(out.result.final_status, 'COMPLETED')
  assert.equal(gateStatus(lastGate(dir, prep.run_id), 'ai_evaluation'), 'BLOCKED')
})

test('9. a non-AI backend project activates neither AI nor frontend/browser agents', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({})
  await runWorkflow('build-project.js', prep.workflow_args, fakes)
  const sel = selected(dir, prep.run_id)
  for (const a of ['ai-engineer', 'ai-evaluation-engineer', 'frontend-engineer', 'browser-qa-engineer', 'ui-ux-designer', 'mobile-engineer']) assert.ok(!sel.includes(a), a)
  const decisions = manifest(dir, prep.run_id).agent_decisions
  assert.ok(decisions.find(d => d.agent === 'ai-evaluation-engineer').decision === 'not activated')
})

test('10. conflicting writing tasks never run in parallel; independent ones do', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({ conflict: true })
  const together = []
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes, { onConcurrent: s => together.push([...s]) })
  assert.ok(!together.some(s => s.includes('TASK-001') && s.includes('TASK-002')), 'TASK-001 and TASK-002 overlapped')
  assert.ok(together.some(s => s.includes('TASK-001') && s.includes('TASK-003')), 'independent tasks should run concurrently')
  assert.deepEqual(manifest(dir, prep.run_id).conflicts, [['TASK-001', 'TASK-002']])
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
})

test('11. an interrupted run resumes from its checkpoint without redoing completed tasks', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const t1 = team({ twoWaves: true })
  const first = await runWorkflow('build-project.js', prep.workflow_args, t1.fakes, { killWhen: (name, info) => info.taskId === 'TASK-002' })
  assert.ok(first.result.finalize_error, 'session died before finalize')
  assert.equal(manifest(dir, prep.run_id).phase, 'DEVELOPING')
  const resume = py(['resume', '--project', dir])
  assert.equal(resume.ok, true, resume.error)
  assert.deepEqual(resume.reset_tasks, ['TASK-002'])
  const second = await runWorkflow('build-project.js', resume.workflow_args, t1.fakes)
  assert.equal(second.result.final_status, 'COMPLETED', second.result.reason)
  assert.equal(t1.counts['backend-engineer:TASK-001'], 1, 'TASK-001 not re-run')
  assert.equal(t1.counts.analyst, 1, 'analysis not re-run')
  const t2 = tasks(dir, prep.run_id).find(t => t.id === 'TASK-002')
  assert.equal(t2.attempt, 2)
})

test('12. tampered operator relay is detected by checksum and retried', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({})
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes, { tamperOnce: 'gate' })
  assert.equal(out.result.final_status, 'COMPLETED')
  assert.ok(out.logs.some(l => /failed integrity check/.test(l)))
})

test('13. blocking questions pause for approval; resume with answers continues', async () => {
  const dir = newProject()
  const prep = prepare(dir, 'build', ['--overrides-json', '{"auto_accept_assumptions": false}'])
  const t = team({ questions: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, t.fakes)
  assert.equal(out.result.final_status, 'AWAITING_APPROVAL')
  assert.equal(manifest(dir, prep.run_id).phase, 'AWAITING_APPROVAL')
  const resume = py(['resume', '--project', dir, '--approve-note', 'Q-1: EUR'])
  const second = await runWorkflow('build-project.js', resume.workflow_args, t.fakes)
  assert.equal(second.result.final_status, 'COMPLETED', second.result.reason)
  assert.equal(t.counts.analyst, 2)
})

test('14. full-stack example: designer, frontend, backend, database and browser QA participate', async () => {
  const dir = newProject('todo')
  const prep = prepare(dir)
  const { fakes, counts } = team({ fullstack: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  const sel = selected(dir, prep.run_id)
  for (const a of ['ui-ux-designer', 'frontend-engineer', 'backend-engineer', 'database-engineer', 'integration-engineer', 'browser-qa-engineer', 'code-qa-engineer', 'final-reviewer']) assert.ok(sel.includes(a), a)
  assert.equal(counts['browser-qa'] >= 1, true)
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
  assert.equal(gateStatus(lastGate(dir, prep.run_id), 'e2e_tests'), 'PASS')
  assert.ok(counts.integration >= 1, 'integration engineer validated the multi-task integration')
})

test('15. audit mode is read-only and reports findings without COMPLETED', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  await runWorkflow('build-project.js', prep.workflow_args, team({}).fakes)
  const audit = prepare(dir, 'audit')
  const { fakes } = team({ securityCritical: true })
  const before = readFileSync(join(dir, 'src', 'app.js'), 'utf8')
  const out = await runWorkflow('audit-project.js', audit.workflow_args, fakes)
  assert.equal(out.result.final_status, 'NEEDS_HUMAN_REVIEW')
  assert.equal(readFileSync(join(dir, 'src', 'app.js'), 'utf8'), before)
  const sel = selected(dir, audit.run_id)
  assert.ok(!sel.includes('backend-engineer') && sel.includes('security-engineer'))
})

test('16. repair mode fixes only user-approved findings', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  await runWorkflow('build-project.js', prep.workflow_args, team({}).fakes)
  const audit = prepare(dir, 'audit')
  await runWorkflow('audit-project.js', audit.workflow_args, team({ securityCritical: true }).fakes)
  const issues = readdirSync(join(dir, '.devpipeline', 'runs', audit.run_id, 'issues')).filter(f => /^ISSUE-\d+\.json$/.test(f))
  const sec = issues.map(f => JSON.parse(readFileSync(join(dir, '.devpipeline', 'runs', audit.run_id, 'issues', f), 'utf8'))).find(i => i.category === 'security')
  assert.ok(sec, 'audit recorded the security finding')
  const bad = py(['prepare', '--project', dir, '--mode', 'repair'])
  assert.equal(bad.ok, false, 'repair without approved scope is refused')
  const rep = prepare(dir, 'repair', ['--source-run', audit.run_id, '--issues', sec.id])
  const { fakes } = team({})
  const out = await runWorkflow('repair-project.js', rep.workflow_args, fakes)
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
  const ts = tasks(dir, rep.run_id)
  assert.equal(ts.length, 1)
  assert.deepEqual(ts[0].owned_paths, ['src/app.js'])
})

// ---- regression scenarios from the independent verification ----
test('17. strict plan approval cannot be bypassed by resuming without approval', async () => {
  const dir = newProject()
  const prep = prepare(dir, 'build', ['--profile', 'strict'])
  const t = team({})
  const first = await runWorkflow('build-project.js', prep.workflow_args, t.fakes)
  assert.equal(first.result.final_status, 'AWAITING_APPROVAL')
  const r1 = py(['resume', '--project', dir])                       // no --approve-note
  const second = await runWorkflow('build-project.js', r1.workflow_args, t.fakes)
  assert.equal(second.result.final_status, 'AWAITING_APPROVAL')
  assert.equal(t.counts['backend-engineer:TASK-001'], undefined, 'no implementation without plan approval')
  const r2 = py(['resume', '--project', dir, '--approve-note', 'plan approved'])
  const third = await runWorkflow('build-project.js', r2.workflow_args, t.fakes)
  assert.equal(t.counts['backend-engineer:TASK-001'], 1)
  assert.equal(t.counts.architect, 1, 'planning not repeated')
  assert.ok(['COMPLETED', 'BLOCKED', 'NEEDS_HUMAN_REVIEW'].includes(third.result.final_status))
  assert.equal(manifest(dir, prep.run_id).approvals.at(-1).kind, 'plan')
})

test('18. a run interrupted during a repair iteration resumes and completes', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const t = team({ bugFirstAttempt: true })
  const first = await runWorkflow('build-project.js', prep.workflow_args, t.fakes, { killWhen: (name, info) => /-R1$/.test(info.taskId || '') })
  assert.ok(first.result.finalize_error)
  const resume = py(['resume', '--project', dir])
  assert.equal(resume.ok, true, resume.error)
  const second = await runWorkflow('build-project.js', resume.workflow_args, t.fakes)
  assert.equal(second.result.final_status, 'COMPLETED', second.result.reason)
})

test('19. a failed repair attempt is superseded by a later successful repair', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({ bugFirstAttempt: true, failFirstRepair: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
  const r1 = tasks(dir, prep.run_id).find(t => /-R1$/.test(t.id))
  assert.equal(r1.status, 'CANCELLED')
  assert.match(r1.failure_reason, /^superseded/)
})

test('20. changes to the user checkout during a run prevent COMPLETED', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes } = team({ touchUserCheckout: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.notEqual(out.result.final_status, 'COMPLETED')
  const m = manifest(dir, prep.run_id)
  assert.ok(m.ownership_violations.some(v => v.scope === 'user-checkout' && v.files.includes('USER_FILE_TOUCHED.txt')))
  assert.equal(tasks(dir, prep.run_id).filter(t => t.kind === 'repair').length, 0, 'no futile repair tasks')
  assert.match(out.result.reason, /unsafe to modify/)
})

test('21. a persistently corrupted relay blocks (resumable) with diagnostics, never re-runs the operation, and resume completes', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const t = team({})
  const first = await runWorkflow('build-project.js', prep.workflow_args, t.fakes, { tamperAlways: 'finish-tasks' })
  assert.equal(first.result.final_status, 'BLOCKED', first.result.reason)
  assert.match(first.result.reason, /integrity check 3 times/)
  const opsDir = join(dir, '.devpipeline', 'runs', prep.run_id, 'ops')
  const diag = readdirSync(opsDir).find(f => f.startsWith('relay-failure-'))
  assert.ok(diag, 'diagnostics written')
  assert.match(readFileSync(join(opsDir, diag), 'utf8'), /extra in relay/)
  const t1 = tasks(dir, prep.run_id).find(x => x.id === 'TASK-001')
  assert.equal(t1.status, 'COMPLETED', 'the operation ran exactly once and its state is kept')
  assert.equal(t1.attempt, 1)
  const resume = py(['resume', '--project', dir])
  const second = await runWorkflow('build-project.js', resume.workflow_args, t.fakes)
  assert.equal(second.result.final_status, 'COMPLETED', second.result.reason)
  assert.equal(t.counts['backend-engineer:TASK-001'], 1)
})

test('22. field report: Haiku repeats the same mis-copy of finish-tasks — retry escalates to another model and the run completes', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes, counts } = team({ twoWaves: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes, { haikuMiscopies: 'finish-tasks' })
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
  assert.ok(out.calls.some(c => c.name === 'pipeline-operator' && c.label === 'op:finish-tasks' && c.model === 'sonnet'))
  assert.equal(counts['backend-engineer:TASK-001'], 1)
  const events = readFileSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'events.jsonl'), 'utf8')
  assert.equal((events.match(/"TASK_COMPLETED".*"TASK-001"|"task_id": "TASK-001".*"TASK_COMPLETED"/g) || []).length, 1, 'finish-tasks executed once despite retries')
  const raw = readdirSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'ops')).find(f => f.includes('finish-tasks'))
  const rec = JSON.parse(readFileSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'ops', raw), 'utf8'))
  assert.ok(!('tasks' in rec) && Array.isArray(rec.updates), 'finish-tasks relays only deltas')
})

test('23. sloppy but correct agent results are normalised instead of retried (field report: artifacts as strings)', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const { fakes, counts } = team({ sloppyResults: true })
  const out = await runWorkflow('build-project.js', prep.workflow_args, fakes)
  assert.equal(out.result.final_status, 'COMPLETED', out.result.reason)
  assert.equal(counts['backend-engineer:TASK-001'], 1, 'no retry needed')
  const res = JSON.parse(readFileSync(join(dir, '.devpipeline', 'runs', prep.run_id, 'agent-results', 'TASK-001.json'), 'utf8'))
  assert.equal(res.agent, 'backend-engineer')
  assert.deepEqual(res.artifacts, [{ path: 'docs/design/index.js' }])
  assert.equal(res.validation[0].evidence_path, 'logs/x.log')
})

test('24. --reuse-plan continues from an earlier run\'s validated plan without re-running analyst/architect', async () => {
  const dir = newProject()
  const prep = prepare(dir)
  const t = team({})
  const first = await runWorkflow('build-project.js', prep.workflow_args, t.fakes, { killWhen: (name) => name === 'backend-engineer' })
  assert.ok(first.result.finalize_error)
  const again = py(['prepare', '--project', dir, '--mode', 'build', '--reuse-plan'])
  assert.equal(again.ok, true, again.error)
  assert.equal(again.workflow_args.reuse_plan, true)
  const second = await runWorkflow('build-project.js', again.workflow_args, t.fakes)
  assert.equal(second.result.final_status, 'COMPLETED', second.result.reason)
  assert.equal(t.counts.analyst, 1)
  assert.equal(t.counts.architect, 1)
})
