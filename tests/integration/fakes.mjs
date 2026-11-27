// Deterministic fake specialist agents for orchestration tests.
// Each fake writes the same artifacts a real agent must write (result JSON,
// reports, verdicts, files in its worktree) so the real deterministic helpers
// validate them exactly as in production.
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'

const w = (path, data) => { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, typeof data === 'string' ? data : JSON.stringify(data, null, 2)) }
const r = path => JSON.parse(readFileSync(path, 'utf8'))

export const PKG_FILES = ['package.json', 'package-lock.json', '.npmrc']

function nodeProject(dir, name, scripts) {
  w(join(dir, 'package.json'), { name, version: '1.0.0', private: true, scripts })
  w(join(dir, 'package-lock.json'), { name, version: '1.0.0', lockfileVersion: 3, requires: true, packages: { '': { name, version: '1.0.0' } } })
  w(join(dir, '.npmrc'), 'audit=false\nfund=false\n')
}

function requirements(opts) {
  const reqs = [{ id: 'REQ-001', title: 'Add numbers', priority: 'must', type: 'functional', description: 'The service adds two numbers.',
    acceptance_criteria: [{ id: 'AC-001', description: 'add(2, 3) returns 5 exactly', verification_method: 'unit' }] }]
  if (opts.ai) reqs.push({ id: 'REQ-002', title: 'Answer questions with citations', priority: 'must', type: 'functional', description: 'Answers cite retrieved sources.',
    acceptance_criteria: [{ id: 'AC-002', description: 'answers include at least one valid source citation', verification_method: 'ai_eval' }] })
  if (opts.fullstack) reqs.push({ id: 'REQ-003', title: 'Task list UI', priority: 'must', type: 'functional', description: 'Users see and add tasks in the browser.',
    acceptance_criteria: [{ id: 'AC-003', description: 'a new task appears in the list after submitting the form', verification_method: 'e2e' }] })
  return {
    project_title: opts.title || 'Calc service', summary: 'Test project', language: 'en', target_users: ['developers'], use_cases: ['add numbers'],
    requirements: reqs, assumptions: ['single user'], non_goals: ['auth'], mvp_scope: ['add'], future_improvements: [],
    features: { backend: true, ai: !!opts.ai, rag: !!opts.ai, frontend: !!opts.fullstack, database: !!opts.fullstack, documentation: true },
    blocking_questions: opts.questions ? [{ id: 'Q-1', question: 'Which currency?', why_blocking: 'changes rounding' }] : [],
  }
}

function plan(opts) {
  const tasks = []
  const shared = PKG_FILES
  if (opts.fullstack) {
    tasks.push({ id: 'TASK-001', agent: 'ui-ux-designer', title: 'Design system', kind: 'design', requirements: ['REQ-003'], dependencies: [], owned_paths: ['docs/design/'], acceptance_criteria: [], status: 'PENDING', attempt: 0 })
    tasks.push({ id: 'TASK-002', agent: 'database-engineer', title: 'Schema', requirements: ['REQ-003'], dependencies: [], owned_paths: ['db/'], acceptance_criteria: [], status: 'PENDING', attempt: 0 })
    tasks.push({ id: 'TASK-003', agent: 'backend-engineer', title: 'Calc API', requirements: ['REQ-001'], dependencies: ['TASK-002'], owned_paths: ['src/', 'test/'], shared_paths: shared, acceptance_criteria: ['AC-001'], status: 'PENDING', attempt: 0 })
    tasks.push({ id: 'TASK-004', agent: 'frontend-engineer', title: 'Task UI', requirements: ['REQ-003'], dependencies: ['TASK-001', 'TASK-003'], owned_paths: ['web/'], acceptance_criteria: ['AC-003'], status: 'PENDING', attempt: 0 })
  } else if (opts.conflict) {
    tasks.push({ id: 'TASK-001', agent: 'backend-engineer', title: 'Calc core', requirements: ['REQ-001'], dependencies: [], owned_paths: ['src/', 'test/'], shared_paths: shared, acceptance_criteria: ['AC-001'], status: 'PENDING', attempt: 0 })
    tasks.push({ id: 'TASK-002', agent: 'backend-engineer', title: 'Calc extras (same dir)', requirements: ['REQ-001'], dependencies: [], owned_paths: ['src/extra.js'], acceptance_criteria: [], status: 'PENDING', attempt: 0 })
    tasks.push({ id: 'TASK-003', agent: 'devops-engineer', title: 'CI config', requirements: [], dependencies: [], owned_paths: ['ci/'], acceptance_criteria: [], status: 'PENDING', attempt: 0 })
  } else {
    tasks.push({ id: 'TASK-001', agent: 'backend-engineer', title: 'Calc core', requirements: ['REQ-001'], dependencies: [], owned_paths: ['src/', 'test/'], shared_paths: shared, acceptance_criteria: ['AC-001'], status: 'PENDING', attempt: 0 })
    if (opts.ai) tasks.push({ id: 'TASK-002', agent: 'ai-engineer', title: 'Answer service', requirements: ['REQ-002'], dependencies: [], owned_paths: ['ai/'], acceptance_criteria: ['AC-002'], status: 'PENDING', attempt: 0 })
    if (opts.twoWaves) tasks.push({ id: 'TASK-002', agent: 'backend-engineer', title: 'Calc docs export', requirements: ['REQ-001'], dependencies: ['TASK-001'], owned_paths: ['src/export/'], acceptance_criteria: [], status: 'PENDING', attempt: 0 })
  }
  const agents = [...new Set(tasks.map(t => t.agent))]
  const arch = {
    project_type: opts.fullstack ? 'fullstack' : opts.ai ? 'ai-backend' : 'backend-api', style: 'modular monolith',
    stack: { backend: 'Node.js', frontend: opts.fullstack ? 'vanilla' : null }, features: requirements(opts).features,
    components: [{ name: 'calc', responsibility: 'arithmetic', paths: ['src/'], owner_agent: 'backend-engineer' }],
    api_contracts: [], env_vars: opts.ai ? [{ name: 'LLM_API_KEY', description: 'model provider key', secret: true, required: false }] : [],
    ownership: agents.map(a => ({ agent: a, paths: tasks.filter(t => t.agent === a).flatMap(t => t.owned_paths) })),
    required_agents: agents, test_strategy: { unit: 'node:test' }, adrs: [], risks: [],
  }
  return { arch, tasks: { tasks } }
}

function result(info, agent, status, changed, extra = {}) {
  const res = { run_id: info.runId, task_id: info.taskId, agent, status, requirements: [], changed_files: changed, artifacts: [],
    validation: [], issues: [], summary: `${agent} ${status}`, ...extra }
  w(info.resultPath, res)
  return { status, summary: res.summary, task_id: info.taskId }
}

function taskDef(info) {
  return r(join(info.runDir, 'tasks.json')).tasks.find(t => t.id === info.taskId)
}

function report(info, agent, kind, status, checks, issues = [], extra = {}) {
  const logPath = join(info.runDir, 'logs', `${kind}-fake.log`)
  w(logPath, 'fake executed evidence\n')
  const rep = { run_id: info.runId, agent, kind, source_fingerprint: info.fingerprint, status,
    checks: checks.map(c => ({ evidence_type: 'executed', exit_code: c.status === 'PASS' ? 0 : 1, log_path: logPath, ...c })), issues, summary: `${kind} ${status}`, ...extra }
  w(info.resultPath, rep)
  return { status, summary: rep.summary }
}

export function team(opts = {}) {
  const counts = {}
  const bump = k => (counts[k] = (counts[k] || 0) + 1)
  const fakes = {
    'product-analyst': async (p, o, info) => { bump('analyst'); w(join(info.project, '.devpipeline', 'requirements.json'), requirements(opts)); w(join(info.project, '.devpipeline', 'PRD.md'), '# PRD\n'); return result(info, 'product-analyst', 'COMPLETED', []) },
    'software-architect': async (p, o, info) => {
      bump('architect')
      if (info.taskId && info.taskId.startsWith('TASK-')) return result(info, 'software-architect', 'COMPLETED', [])
      const { arch, tasks } = plan(opts)
      w(join(info.project, '.devpipeline', 'architecture.json'), arch)
      w(join(info.project, '.devpipeline', 'tasks.json'), tasks)
      w(join(info.project, '.devpipeline', 'ARCHITECTURE.md'), '# Architecture\n')
      return result(info, 'software-architect', 'COMPLETED', [])
    },
    'integration-engineer': async (p, o, info) => { bump('integration'); return result(info, 'integration-engineer', 'COMPLETED', []) },
  }
  const engineer = name => async (p, o, info) => {
    const n = bump(`${name}:${info.taskId}`)
    if (opts.nullResult && name === 'backend-engineer') return null
    if (opts.failFirstRepair && name === 'backend-engineer' && /-R1$/.test(info.taskId)) return null
    if (opts.touchUserCheckout && name === 'backend-engineer') w(join(info.project, 'USER_FILE_TOUCHED.txt'), 'oops')
    const t = taskDef(info)
    const changed = []
    const put = (rel, data) => {
      const full = join(info.cwd, rel)
      const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
      if (existsSync(full) && readFileSync(full, 'utf8') === text) return
      w(full, text); changed.push(rel)
    }
    if (t.owned_paths.includes('src/')) {
      const bad = opts.neverFix || (opts.bugFirstAttempt && t.kind !== 'repair')
      const scripts = { test: 'node --test test/', build: 'node --check src/app.js', lint: 'node --check src/app.js' }
      if (opts.fullstack) scripts['test:e2e'] = 'node --test e2e/'
      put('package.json', { name: 'calc', version: '1.0.0', private: true, scripts })
      put('package-lock.json', { name: 'calc', version: '1.0.0', lockfileVersion: 3, requires: true, packages: { '': { name: 'calc', version: '1.0.0' } } })
      put('.npmrc', 'audit=false\nfund=false\n')
      put('src/app.js', bad ? 'exports.add = (a, b) => a - b\n' : 'exports.add = (a, b) => a + b\n')
      const skip = opts.skipTest ? "test.skip('pending behaviour', () => {})\n" : ''
      put('test/app.test.js', "const test = require('node:test')\nconst assert = require('node:assert')\nconst { add } = require('../src/app.js')\ntest('adds', () => assert.strictEqual(add(2, 3), 5))\n" + skip)
    } else {
      for (const pth of t.owned_paths) put(pth.endsWith('/') ? pth + 'index.js' : pth, `// ${t.title} (attempt ${n})\nmodule.exports = {}\n`)
    }
    return result(info, name, 'COMPLETED', [...new Set(changed)])
  }
  for (const n of ['backend-engineer', 'frontend-engineer', 'database-engineer', 'ai-engineer', 'ui-ux-designer', 'devops-engineer', 'mobile-engineer']) fakes[n] = engineer(n)
  fakes['code-qa-engineer'] = async (p, o, info) => { bump('code-qa'); return report(info, 'code-qa-engineer', 'code_qa', 'PASS', [{ name: 'unit-review', status: 'PASS', requirement_ids: ['REQ-001'] }]) }
  fakes['security-engineer'] = async (p, o, info) => {
    bump('security')
    if (opts.securityCritical) {
      return report(info, 'security-engineer', 'security', 'FAIL', [{ name: 'authz-review', status: 'FAIL', evidence_type: 'static_analysis' }],
        [{ id: 'ISSUE-900', source_agent: 'security-engineer', assigned_agent: 'backend-engineer', severity: 'critical', category: 'security', title: 'Command injection in add', description: 'User input reaches child_process.exec in src/app.js', affected_files: ['src/app.js'], blocking: true, status: 'OPEN' }],
        { scanners: [{ name: 'semgrep', available: false, ran: false, status: 'NOT_RUN', reason: 'not installed' }] })
    }
    return report(info, 'security-engineer', 'security', 'PASS', [{ name: 'owasp-review', status: 'PASS', evidence_type: 'static_analysis' }], [],
      { scanners: [{ name: 'semgrep', available: false, ran: false, status: 'NOT_RUN', reason: 'not installed' }] })
  }
  fakes['browser-qa-engineer'] = async (p, o, info) => {
    bump('browser-qa')
    w(join(info.cwd, 'e2e', 'ui.test.js'), "const test = require('node:test')\ntest('task appears', () => {})\n")
    return report(info, 'browser-qa-engineer', 'browser_qa', 'PASS', [{ name: 'journey:add-task', status: 'PASS', evidence_type: 'observed', requirement_ids: ['REQ-003'] }], [], { viewports: ['390x844', '1440x900'] })
  }
  fakes['ai-evaluation-engineer'] = async (p, o, info) => {
    bump('ai-eval')
    return report(info, 'ai-evaluation-engineer', 'ai_eval', 'PASS', [
      { name: 'structured-output-validity', status: 'PASS' },
      { name: 'prompt-injection-resistance', status: 'PASS' },
      { name: 'live:answer-quality', status: 'NOT_RUN', evidence_type: 'mock', exit_code: null, log_path: undefined, reason: 'no credentials' }])
  }
  fakes['performance-engineer'] = async (p, o, info) => { bump('perf'); return report(info, 'performance-engineer', 'performance', 'PASS', [{ name: 'latency-p50', status: 'PASS' }]) }
  const reviewer = (name, kind) => async (p, o, info) => {
    bump(name)
    const req = r(join(info.project, '.devpipeline', 'requirements.json'))
    const matrix = req.requirements.flatMap(q => q.acceptance_criteria.map(a => ({ ac_id: a.id, requirement_id: q.id, status: 'VERIFIED', evidence: ['test-reports'] })))
    const v = { run_id: info.runId, kind, agent: name, approved: true, status: 'APPROVED', source_fingerprint: info.fingerprint, requirements_total: req.requirements.length,
      requirements_verified: req.requirements.length, missing_requirements: [], acceptance_matrix: kind === 'final_review' ? matrix : [], blocking_issues: [], non_blocking_issues: [], required_repairs: [], evidence: [], summary: `${kind} ok` }
    w(info.resultPath, v)
    return { status: 'APPROVED', summary: v.summary }
  }
  fakes['code-reviewer'] = reviewer('code-reviewer', 'code_review')
  fakes['final-reviewer'] = reviewer('final-reviewer', 'final_review')
  fakes['technical-writer'] = async (p, o, info) => {
    bump('writer')
    const lines = ['# Calc service', '', 'A tiny service used by DevPipeline tests.', '', '## Installation', '', '```bash', 'npm ci', '```', '', '## Running', '', '```bash', 'npm run build', '```', '',
      '## Testing', '', '```bash', 'npm test', '```', '', '## Configuration', '', '- `LLM_API_KEY` — model provider key (optional, never committed).', '', '## Architecture', '', 'Modular monolith.', '', '## Limitations', '', '- Demo only.', '', '## Troubleshooting', '', '- Run `npm ci` again.', '- Delete node_modules and reinstall.', '- Check the Node.js version (>= 20).', '', '## Features', '', '- Addition endpoint (verified).', '- Documentation of limitations.']
    w(join(info.cwd, 'README.md'), lines.join('\n') + '\n')
    return result(info, 'technical-writer', 'COMPLETED', ['README.md'])
  }
  return { fakes, counts }
}
