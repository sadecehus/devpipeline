// ---------------------------------------------------------------------------
// DevPipeline workflow engine (shared by build/improve/audit/repair).
// GENERATED INTO workflows/*.js by scripts/build_workflows.py — edit this file,
// then run `python3 scripts/build_workflows.py`. tests/unit/test_hooks_install.py (SourceSyncTests)
// fails when the generated files drift from this source.
//
// Runtime facts (Claude Code dynamic workflows): plain JavaScript, no imports,
// no filesystem/shell, Date.now()/Math.random()/argless Date throw. All file
// IO and deterministic decisions therefore go through scripts/devpipeline.py,
// executed by the narrow `pipeline-operator` agent whose relayed output is
// integrity-checked (FNV-1a checksum over canonical JSON).
// ---------------------------------------------------------------------------

const DP = args || {}
if (!DP.plugin_root || !DP.project_dir || !DP.run_id) {
  throw new Error('DevPipeline workflows need args {plugin_root, project_dir, run_id}. Start them through /devpipeline:build-project (or improve/audit/repair/resume).')
}
const PFX = typeof DP.agent_prefix === 'string' ? DP.agent_prefix : 'devpipeline:'
let QA_KIND = { 'code-qa-engineer': 'code_qa', 'browser-qa-engineer': 'browser_qa', 'security-engineer': 'security', 'ai-evaluation-engineer': 'ai_eval', 'performance-engineer': 'performance' }  // replaced by config/agent-registry.json via context
const TERMINAL = ['COMPLETED', 'CANCELLED', 'FAILED']
const STOP_STATES = ['BLOCKED', 'NEEDS_HUMAN_REVIEW', 'BUDGET_EXHAUSTED', 'FAILED', 'CANCELLED']

const counters = { agent_calls: 0, operator_calls: 0 }
let CFG = { max_agent_calls: 80, max_operator_calls: 200, max_parallel_agents: 3, max_task_retries: 2, max_repair_iterations: 5, model_routing: {}, effort_routing: {}, viewports: [] }
let CTX = null
let PHASE = 'INITIALIZING'
let CONFLICTS = []

class StopRun extends Error {
  constructor(status, reason) { super(reason); this.status = status; this.reason = reason }
}

// ------------------------------------------------------------ encoding ----
function utf8Bytes(str) {
  const out = []
  for (let i = 0; i < str.length; i++) {
    let c = str.charCodeAt(i)
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < str.length) {
      const d = str.charCodeAt(i + 1)
      if (d >= 0xdc00 && d <= 0xdfff) { c = 0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00); i++ }
    }
    if (c < 0x80) out.push(c)
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63))
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63))
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63))
  }
  return out
}
const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
function b64json(value) {
  const bytes = utf8Bytes(JSON.stringify(value))
  let s = ''
  for (let i = 0; i < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | ((bytes[i + 1] || 0) << 8) | (bytes[i + 2] || 0)
    s += B64[(n >> 18) & 63] + B64[(n >> 12) & 63] + (i + 1 < bytes.length ? B64[(n >> 6) & 63] : '=') + (i + 2 < bytes.length ? B64[n & 63] : '=')
  }
  return s
}
function asciiJsonString(str) {
  return JSON.stringify(str).replace(/[\u007f-￿]/g, ch => '\\u' + ch.charCodeAt(0).toString(16).padStart(4, '0'))
}
function canonical(v) {
  if (v === null || v === undefined) return 'null'
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']'
  if (typeof v === 'object') return '{' + Object.keys(v).sort().map(k => asciiJsonString(k) + ':' + canonical(v[k])).join(',') + '}'
  if (typeof v === 'string') return asciiJsonString(v)
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : asciiJsonString(String(v))
  return JSON.stringify(v)
}
function fnv1a32(str) {
  let h = 0x811c9dc5
  for (const b of utf8Bytes(str)) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0 }
  return h.toString(16).padStart(8, '0')
}
function checksumOk(obj) {
  if (!obj || typeof obj !== 'object' || typeof obj.checksum !== 'string') return false
  const copy = {}
  for (const k of Object.keys(obj)) if (k !== 'checksum') copy[k] = obj[k]
  return fnv1a32(canonical(copy)) === obj.checksum
}
function q(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'" }

// ------------------------------------------------------------ operator ----
const OP_SCHEMA = { type: 'object', required: ['exit_code', 'output'], properties: { exit_code: { type: 'integer' }, output: { type: 'object' } } }

async function op(cmd, extra) {
  const parts = ['python3', q(DP.plugin_root + '/scripts/devpipeline.py'), cmd, '--project', q(DP.project_dir), '--run', q(DP.run_id)].concat(extra || [])
  const command = parts.join(' ')
  for (let attempt = 1; attempt <= 3; attempt++) {
    if (counters.operator_calls >= CFG.max_operator_calls) throw new StopRun('BUDGET_EXHAUSTED', `operator call limit (${CFG.max_operator_calls}) reached`)
    counters.operator_calls++
    let r = null
    try {
      r = await agent(`Run this DevPipeline helper exactly once and relay its JSON.\n<command>${command}</command>`,
        { agentType: PFX + 'pipeline-operator', label: `op:${cmd}`, phase: PHASE, schema: OP_SCHEMA, model: 'haiku', effort: 'low' })
    } catch (e) {
      log(`operator call for ${cmd} failed (attempt ${attempt}): ${e && e.message}`)
      continue
    }
    if (r && checksumOk(r.output)) return r.output
    log(`operator relay for ${cmd} failed integrity check (attempt ${attempt}); retrying`)
  }
  throw new StopRun('FAILED', `deterministic helper '${cmd}' could not be executed/relayed after 3 attempts`)
}

async function mustOp(cmd, extra) {
  const out = await op(cmd, extra)
  if (out.ok === false) throw new StopRun('FAILED', `${cmd}: ${out.error}`)
  return out
}

async function to(phase, reason, step, awaiting) {
  const extra = ['--to', phase, '--reason', q(reason || ''), '--counters-b64', b64json(counters)]
  if (step) extra.push('--step', step)
  if (awaiting) extra.push('--awaiting', awaiting)
  const out = await op('transition', extra)
  if (out.ok === false && /max_runtime_minutes/.test(out.error || '')) throw new StopRun('BUDGET_EXHAUSTED', out.error)
  if (out.ok === false) throw new StopRun('FAILED', `state transition to ${phase} refused: ${out.error}`)
  PHASE = phase
  return out
}
async function markStep(step) { return to(PHASE, `step ${step} done`, step) }
function done(step) { return CTX && CTX.completed_steps && CTX.completed_steps.indexOf(step) >= 0 }

// ---------------------------------------------------------- specialists ----
const RESULT_SCHEMA = {
  type: 'object', required: ['status', 'summary'],
  properties: { status: { enum: ['COMPLETED', 'FAILED', 'BLOCKED', 'PARTIAL', 'NEEDS_INPUT'] }, summary: { type: 'string' }, task_id: { type: 'string' } }
}
const REPORT_SCHEMA = {
  type: 'object', required: ['status', 'summary'],
  properties: { status: { enum: ['PASS', 'FAIL', 'SKIPPED', 'NOT_RUN', 'BLOCKED', 'ERROR', 'NOT_APPLICABLE', 'APPROVED', 'REPAIR_REQUIRED', 'NEEDS_HUMAN_REVIEW'] }, summary: { type: 'string' } }
}

function budgetCheck() {
  if (counters.agent_calls >= CFG.max_agent_calls) throw new StopRun('BUDGET_EXHAUSTED', `agent call limit (${CFG.max_agent_calls}) reached`)
  if (CFG.max_output_tokens && typeof budget === 'object' && budget && typeof budget.spent === 'function' && budget.spent() > CFG.max_output_tokens) {
    throw new StopRun('BUDGET_EXHAUSTED', `output token limit (${CFG.max_output_tokens}) reached`)
  }
}

async function specialist(name, prompt, opts) {
  budgetCheck()
  counters.agent_calls++
  const o = { agentType: PFX + name, label: (opts && opts.label) || name, phase: PHASE, schema: (opts && opts.schema) || RESULT_SCHEMA }
  if (CFG.model_routing && CFG.model_routing[name]) o.model = CFG.model_routing[name]
  if (CFG.effort_routing && CFG.effort_routing[name]) o.effort = CFG.effort_routing[name]
  try {
    return await agent(prompt, o)
  } catch (e) {
    log(`${name} (${o.label}) failed: ${e && e.message}`)
    return null
  }
}

function P() { return CTX.paths }
function header(taskId, cwd, resultPath) {
  return [
    `DevPipeline run ${DP.run_id} — project ${DP.project_dir}`,
    `Task id: ${taskId}`,
    `Working directory: ${cwd}  (use absolute paths inside it; run commands as \`cd ${q(cwd)} && ...\`)`,
    `Result path: ${resultPath}`,
    `Planning files: ${P().dp}/requirements.json, ${P().dp}/PRD.md, ${P().dp}/architecture.json, ${P().dp}/ARCHITECTURE.md, ${P().dp}/tasks.json, contracts in ${P().contracts}`,
    `Run directory (logs, screenshots, artifacts): ${P().run_dir}`,
    `Schemas: ${P().schemas}   Templates: ${P().templates}`,
  ].join('\n')
}

// ------------------------------------------------------------ planning ----
async function analysis() {
  if (done('analysis')) return
  await to('ANALYZING', 'requirements analysis')
  let errors = []
  let v = null
  for (let attempt = 1; attempt <= CFG.max_task_retries + 1; attempt++) {
    const prompt = header('PLAN-ANALYSIS', P().project, `${P().run_dir}/agent-results/PLAN-ANALYSIS.json`) + `\n
Mode: ${CTX.mode} (${CTX.project_mode} project). The original request is in ${P().request} — read it fully (it may be Turkish or English).
${CTX.project_mode === 'existing' ? 'Inspect the existing repository (read-only) to understand current behaviour, conventions and CLAUDE.md before specifying changes.\n' : ''}Write ${P().dp}/requirements.json (schema ${P().schemas}/requirements.schema.json) and ${P().dp}/PRD.md (template ${P().templates}/PRD.md).
${(CTX.approval_notes || []).length ? 'The user answered/approved: ' + CTX.approval_notes.join(' | ') + '\nIncorporate these answers and remove the resolved blocking_questions.\n' : ''}${errors.length ? 'Your previous attempt was rejected by the validator. Fix ALL of these errors:\n- ' + errors.join('\n- ') : ''}`
    await specialist('product-analyst', prompt, { label: `analysis#${attempt}` })
    v = await mustOp('validate-requirements')
    if (v.valid) break
    errors = v.errors || []
  }
  if (!v || !v.valid) throw new StopRun('NEEDS_HUMAN_REVIEW', 'requirements could not be validated: ' + (v ? v.errors.slice(0, 5).join('; ') : 'no result'))
  const questions = (v.blocking_questions || [])
  const unanswered = questions.filter(x => !(CFG.auto_accept_assumptions && x.default_if_unanswered))
  if (unanswered.length && !CTX.questions_approved) {
    await to('AWAITING_APPROVAL', `${unanswered.length} blocking question(s)`, null, 'questions')
    throw new StopRun('AWAITING_APPROVAL', unanswered.map(x => `${x.id}: ${x.question}`).join(' | '))
  }
  if (questions.length) log(`accepted defaults for ${questions.length} question(s) as recorded assumptions`)
  await markStep('analysis')
}

async function planning() {
  if (done('planning')) return { tasks: CTX.tasks, conflicts: CTX.conflicts, selected_agents: CTX.selected_agents }
  await to('PLANNING', 'architecture and task graph')
  let errors = []
  let plan = null
  for (let attempt = 1; attempt <= CFG.max_task_retries + 1; attempt++) {
    const prompt = header('PLAN-ARCHITECTURE', P().project, `${P().run_dir}/agent-results/PLAN-ARCHITECTURE.json`) + `\n
Design the architecture for the validated requirements. Produce ${P().dp}/architecture.json (schema architecture.schema.json), ${P().dp}/ARCHITECTURE.md, ADRs in ${P().dp}/adr/, contracts in ${P().contracts}/ and the task DAG in ${P().dp}/tasks.json (schema task.schema.json, all tasks status PENDING, attempt 0).
Profile: ${CFG.profile}. Max parallel writers: ${CFG.max_parallel_agents}. Isolation: ${CTX.isolation} (owned_paths are relative to the repository root).
${CTX.project_mode === 'existing' ? 'Existing project: keep the current stack and conventions; plan a scoped change, not a rewrite.\n' : ''}${errors.length ? 'The plan validator rejected your previous attempt. Fix ALL of these errors:\n- ' + errors.join('\n- ') : ''}`
    await specialist('software-architect', prompt, { label: `architecture#${attempt}` })
    plan = await mustOp('validate-plan')
    if (plan.valid) break
    errors = plan.errors || []
  }
  if (!plan || !plan.valid) throw new StopRun('NEEDS_HUMAN_REVIEW', 'plan could not be validated: ' + (plan ? plan.errors.slice(0, 5).join('; ') : 'no result'))
  for (const w of plan.warnings || []) log(`plan: ${w}`)
  log(`agents: ${plan.selected_agents.join(', ')}`)
  await markStep('planning')
  return plan
}

async function requirePlanApproval(plan) {
  // checked on every (re)entry, independent of the planning step, and only a *plan* approval satisfies it
  if (CFG.require_plan_approval && !CTX.plan_approved) {
    await to('AWAITING_APPROVAL', 'plan approval required by profile', null, 'plan')
    throw new StopRun('AWAITING_APPROVAL', `plan ready for approval: ${plan.tasks.length} task(s); agents ${(plan.selected_agents || []).join(', ')}`)
  }
}

// ----------------------------------------------------------- scheduling ----
function pickReady(tasks, filter, limit) {
  const status = {}
  for (const t of tasks) status[t.id] = t.status
  const picked = []
  const sorted = tasks.slice().sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  for (const t of sorted) {
    if (!filter(t) || t.status !== 'PENDING') continue
    if (t.deps.some(d => status[d] !== 'COMPLETED')) continue
    const clash = picked.some(o => CONFLICTS.some(c => (c[0] === t.id && c[1] === o) || (c[1] === t.id && c[0] === o)))
    if (clash) continue
    picked.push(t.id)
    if (picked.length >= limit) break
  }
  return picked
}

function taskPrompt(s) {
  const repair = s.kind === 'repair' || s.kind === 'contract'
  return header(s.task_id, s.worktree, s.result_path) + `\n
Your task: ${s.task_id} "${s.title}" (attempt ${s.attempt}). Read its full definition in ${P().run_dir}/tasks.json.
Owned paths (only these may change): ${JSON.stringify(s.owned_paths)}
${repair ? `This is a REPAIR task for issues ${s.issue_ids.join(', ')} — read each in ${P().run_dir}/issues/<ID>.json. Fix root causes; never weaken tests, remove requirements or change contracts to make checks pass.\n` : ''}${s.kind === 'contract' ? `Contract task: update ${P().contracts}/ and append the change with reason to ${P().dp}/contract-changes.json; do not edit application code.\n` : ''}${s.failure_reason ? `Previous attempt was rejected: ${s.failure_reason}\n` : ''}Do not commit — the coordinator commits your worktree and merges it. Finish by writing the agent-result JSON to the result path and returning it.`
}

async function resolveConflicts(conflicts) {
  const ids = conflicts.map(c => c.task_id)
  const prompt = header('INTEGRATION', P().integration, `${P().run_dir}/agent-results/INTEGRATION.json`) + `\n
Automatic merge of these task branches into the integration worktree failed:
${conflicts.map(c => `- ${c.task_id}: branch ${c.branch}; conflicting files ${JSON.stringify(c.files)}`).join('\n')}
In ${P().integration} run \`git merge --no-ff <branch>\` for each, resolve conflicts preserving both sides' intent, \`git add\` the files and conclude with \`git commit --no-edit\`. Never drop either side; contract conflicts → issue to software-architect.`
  await specialist('integration-engineer', prompt, { label: `merge:${ids.join(',')}` })
  return mustOp('verify-merge', ['--tasks', ids.join(',')])
}

async function schedule(tasks, filter) {
  const limit = CTX.isolation === 'worktree' ? Math.max(1, CFG.max_parallel_agents) : 1
  for (let guard = 0; guard < 200; guard++) {
    const pending = tasks.filter(t => filter(t) && t.status === 'PENDING')
    if (!pending.length) break
    const ready = pickReady(tasks, filter, limit)
    if (!ready.length) { log(`no runnable task among ${pending.length} pending (dependencies failed or blocked)`); break }
    const st = await mustOp('start-tasks', ['--tasks', ready.join(',')])
    log(`wave: ${ready.join(', ')}`)
    const results = await parallel(st.started.map(s => () => specialist(s.agent, taskPrompt(s), { label: s.task_id })))
    const returned = {}
    st.started.forEach((s, i) => { returned[s.task_id] = results[i] ? results[i].status : null })
    let fin = await mustOp('finish-tasks', ['--tasks', ready.join(','), '--returned-b64', b64json(returned)])
    for (const r of fin.results) if (r.status !== 'COMPLETED') log(`${r.id}: ${r.status}${r.retry ? ' (will retry)' : ''} ${r.errors.join('; ')}`)
    tasks = fin.tasks
    if (fin.conflicts && fin.conflicts.length) tasks = (await resolveConflicts(fin.conflicts)).tasks
  }
  return tasks
}

// --------------------------------------------------- integration and QA ----
async function integrate(label, withEngineer) {
  await to('INTEGRATING', label)
  const st = await mustOp('integration-status')
  if (st.unmerged.length) {
    await resolveConflicts(st.unmerged.map(id => ({ task_id: id, branch: `devpipeline/${DP.run_id}/${id}`, files: [] })))
  }
  if (withEngineer) {
    const prompt = header('INTEGRATION', P().integration, `${P().run_dir}/agent-results/INTEGRATION.json`) + `\n
All task branches are merged into the integration worktree ${P().integration} (HEAD ${st.integration_head}).
Validate the integrated result: manifests/lockfiles consistent (lockfile installs succeed), shared types compile, API usage matches the contracts, env vars match .env.example, migrations apply on a fresh test database, the integrated build passes and the app boots per architecture.app_start (local ports only; stop what you start).
Only integration glue may change here; route feature defects as issues to their owners. Record evidence in the agent result.`
    await specialist('integration-engineer', prompt, { label: label })
    await mustOp('commit-integration', ['--scope', 'integration', '--message', q(`devpipeline(${DP.run_id}): integration ${label}`)])
  }
}

function qaPrompt(name, fp, mode) {
  const kind = QA_KIND[name]
  const path = `${P().run_dir}/test-reports/${kind}.json`
  return header(`QA-${kind.toUpperCase().replace('_', '-')}`, P().integration, path) + `\n
You are the independent ${name}. Evaluate the integrated revision in ${P().integration}.
Report kind: ${kind}. Write the report (schema test-report.schema.json) to ${path} with "source_fingerprint": "${fp}" and "run_id": "${DP.run_id}".
${mode === 'audit' ? 'AUDIT MODE: read-only — do not create or modify any file outside the run directory; report gaps as issues.\n' : 'You may add tests only in QA test paths (tests/, e2e/, evals/, *.test.*, *.spec.*, playwright.config.*). Never edit production code.\n'}${name === 'browser-qa-engineer' ? `Viewports: ${JSON.stringify(CFG.viewports)}. Start the app per architecture.app_start (or the README) on localhost only. Ensure a test:e2e script runs your Playwright suite.\n` : ''}${name === 'ai-evaluation-engineer' ? `Live model evaluation policy: ${CFG.live_ai_evaluation}. Name live checks "live:<name>"; without credentials+approval mark them NOT_RUN.\n` : ''}Issues: use issue.schema.json with assigned_agent set to the responsible engineer. Return {"status": <report status>, "summary": "..."}.`
}

async function qaFanout(qaAgents, fp, mode) {
  if (!qaAgents.length) return
  const width = Math.max(1, CFG.max_parallel_agents)
  for (let i = 0; i < qaAgents.length; i += width) {
    const chunk = qaAgents.slice(i, i + width)
    await parallel(chunk.map(a => () => specialist(a, qaPrompt(a, fp, mode), { label: `qa:${a}`, schema: REPORT_SCHEMA })))
  }
  const chk = await mustOp('validate-reports', ['--expect', qaAgents.map(a => `${QA_KIND[a]}:${a}`).join(',')])
  const retry = chk.missing.concat(chk.invalid.map(x => x.agent))
  if (retry.length) {
    log(`re-running QA with missing/invalid reports: ${retry.join(', ')}`)
    await parallel(retry.map(a => () => specialist(a, qaPrompt(a, fp, mode) + '\nYour previous report was missing or failed schema validation; write a valid report.', { label: `qa-retry:${a}`, schema: REPORT_SCHEMA })))
  }
}

function reviewPrompt(name, kind, fp) {
  const path = `${P().run_dir}/reviews/${kind}.json`
  return header(`REVIEW-${kind === 'code_review' ? 'CODE' : 'FINAL'}`, P().integration, path) + `\n
Independent ${kind.replace('_', ' ')} of the integrated revision in ${P().integration} (base revision ${CTX.source.base_revision || 'n/a'}).
Evidence: check reports, QA reports and issues in ${P().run_dir}/ (test-reports/, issues/, gates/), task results in agent-results/, original request ${P().request}.
Write the verdict (schema review.schema.json, "kind": "${kind}") to ${path} with "source_fingerprint": "${fp}". Return {"status": <verdict status>, "summary": "..."}.`
}

async function verification(mode, label) {
  await to('TESTING', label)
  const pre = await mustOp('run-checks', ['--label', q(`${label}-pre`)])
  if (mode !== 'audit' && pre.selected_agents) CTX.selected_agents = pre.selected_agents
  for (const a of pre.added_agents || []) log(`activated ${a}: AI functionality detected in code`)
  const qa = (CTX.selected_agents || []).filter(a => QA_KIND[a])
  await qaFanout(qa, pre.prod_fingerprint, mode)
  if (mode !== 'audit') await mustOp('commit-integration', ['--scope', 'qa', '--message', q(`devpipeline(${DP.run_id}): QA tests ${label}`)])
  const post = await mustOp('run-checks', ['--label', q(`${label}-post`)])
  log(`checks: ${post.status}${post.failing.length ? ' — failing: ' + post.failing.join(', ') : ''}`)
  await to('REVIEWING', label)
  if ((CTX.selected_agents || []).indexOf('code-reviewer') >= 0) {
    await specialist('code-reviewer', reviewPrompt('code-reviewer', 'code_review', post.fingerprint), { label: `review:${label}`, schema: REPORT_SCHEMA })
  }
  if (mode !== 'audit') {
    await specialist('final-reviewer', reviewPrompt('final-reviewer', 'final_review', post.fingerprint), { label: `final:${label}`, schema: REPORT_SCHEMA })
  }
  return mustOp('gate')
}

async function documentation(label) {
  await to('DOCUMENTING', label)
  const prompt = header('DOC-README', P().integration, `${P().run_dir}/agent-results/DOC-README.json`) + `\n
Write/update README.md (and docs/*.md, .env.example) in ${P().integration} so that it documents exactly what exists: purpose, installation, env vars (names only), dev/test/build commands, architecture, API usage, database setup, AI configuration, deployment procedure (not executed), limitations, troubleshooting and completed vs incomplete features (latest gate: ${P().run_dir}/gates/, final review: ${P().run_dir}/reviews/final_review.json).
Only documentation files may change. Verify every command you document exists in the manifests.`
  await specialist('technical-writer', prompt, { label: `docs:${label}` })
  await mustOp('commit-integration', ['--scope', 'docs', '--message', q(`devpipeline(${DP.run_id}): documentation ${label}`)])
  return mustOp('gate')
}

function onlyDocsFailing(gate) {
  // documentation is produced after everything else passes; its gate (and the blocking issue it raises) is expected to fail before that
  const cats = gate.blocking_categories || []
  return gate.failing_mandatory.indexOf('documentation') >= 0 &&
    gate.failing_mandatory.every(g => g === 'documentation' || (g === 'open_blocking_issues' && cats.every(c => c === 'documentation')))
}

// --------------------------------------------------------- repair loop ----
async function repairLoop(gate, mode) {
  let docsAttempts = 0
  const first = ((CTX.counters && CTX.counters.repair_iterations) || 0) + 1
  for (let iteration = first; iteration < first + 50; iteration++) {
    if (gate.passed && mode !== 'build' && mode !== 'improve') return gate
    if (gate.passed || onlyDocsFailing(gate)) {
      if (mode === 'repair') return gate
      if (docsAttempts > CFG.max_task_retries) throw new StopRun('NEEDS_HUMAN_REVIEW', 'documentation gate still failing after retries')
      docsAttempts++
      gate = await documentation(`docs-${docsAttempts}`)
      if (gate.passed) return gate
      if (onlyDocsFailing(gate)) { iteration--; continue }
    }
    await to('REPAIRING', `repair iteration ${iteration}: ${gate.failing_mandatory.join(', ')}`)
    const plan = await mustOp('plan-repair', ['--iteration', String(iteration)])
    if (plan.stop) throw new StopRun(plan.status, plan.reason)
    if (plan.stalled && plan.stalled.length) log(`stalled issues skipped: ${plan.stalled.join(', ')}`)
    CONFLICTS = CONFLICTS.concat(plan.conflicts || [])
    let tasks = plan.repair_tasks
    if (tasks.length) {
      log(`repair ${iteration}: ${tasks.map(t => `${t.id}→${t.agent}`).join(', ')}`)
      tasks = await schedule(tasks, () => true)
      await integrate(`repair-${iteration}`, false)
    }
    gate = await verification(mode, `repair-${iteration}`)
  }
  throw new StopRun('NEEDS_HUMAN_REVIEW', 'repair loop guard reached')
}

// ---------------------------------------------------------------- runs ----
async function loadContext() {
  CTX = await mustOp('context')
  PHASE = CTX.phase
  for (const k of Object.keys(CTX.config || {})) if (CTX.config[k] !== null && CTX.config[k] !== undefined) CFG[k] = CTX.config[k]
  counters.agent_calls = Math.max(counters.agent_calls, CTX.counters.agent_calls || 0)
  counters.operator_calls = Math.max(counters.operator_calls, CTX.counters.operator_calls || 0)
  CONFLICTS = CTX.conflicts || []
  if (CTX.qa_kinds && Object.keys(CTX.qa_kinds).length) QA_KIND = CTX.qa_kinds
  for (const w of CTX.warnings || []) log(`note: ${w}`)
  if (TERMINAL.indexOf(PHASE) >= 0) throw new StopRun(PHASE, `run already ${PHASE}`)
}

async function finalize(status, reason) {
  const out = await op('finalize', ['--status', status, '--reason', q(reason || '')])
  return {
    run_id: DP.run_id, final_status: out.final_status || status, reason: out.reason || reason, report: out.report,
    integration_branch: out.integration_branch, merge_hint: out.merge_hint, fast_forwarded: out.fast_forwarded,
    agent_calls: counters.agent_calls, operator_calls: counters.operator_calls,
  }
}

async function runPipeline(mode) {
  try {
    await loadContext()
    if (mode === 'audit') {
      const sel = await mustOp('select-audit')
      CTX.selected_agents = sel.selected_agents
      log(`audit agents: ${sel.selected_agents.join(', ')}`)
      const gate = await verification('audit', 'audit')
      return await finalize(gate.passed ? 'COMPLETED' : 'NEEDS_HUMAN_REVIEW',
        gate.passed ? 'audit found no blocking issues' : `audit findings: ${gate.blocking_issue_ids.join(', ') || gate.failing_mandatory.join(', ')}`)
    }
    if ((mode === 'improve' || mode === 'repair') && !done('baseline')) {
      const b = await mustOp('baseline')
      log(`baseline: ${b.status}${b.failing.length ? ' — pre-existing failures: ' + b.failing.join(', ') : ''}`)
      await markStep('baseline')
    }
    let tasks
    if (mode === 'repair') {
      if (!done('import')) {
        const imp = await mustOp('import-findings', ['--source-run', q(DP.source_run), '--issues', q((DP.issue_ids || []).join(','))])
        CTX.selected_agents = imp.selected_agents
        CONFLICTS = imp.conflicts || []
        tasks = imp.tasks
        await to('REPAIRING', 'approved findings imported', 'import')
      } else {
        tasks = CTX.tasks
        if (PHASE !== 'REPAIRING') await to('REPAIRING', 'resume repair')
      }
      tasks = await schedule(tasks, () => true)
    } else {
      await analysis()
      const plan = await planning()
      CTX.selected_agents = plan.selected_agents
      CONFLICTS = plan.conflicts || CONFLICTS
      tasks = plan.tasks
      await requirePlanApproval(plan)
      if (!done('development')) {
        if (tasks.some(t => t.agent === 'ui-ux-designer' && t.status === 'PENDING')) {
          await to('DESIGNING', 'design system')
          tasks = await schedule(tasks, t => t.agent === 'ui-ux-designer')
        }
        await to('DEVELOPING', 'implementation')
        tasks = await schedule(tasks, () => true)
      } else if (tasks.some(t => t.status === 'PENDING')) {
        // resumed after an interruption (e.g. during a repair iteration): finish the pending tasks first
        await to(tasks.some(t => t.kind === 'repair' && t.status === 'PENDING') ? 'REPAIRING' : 'DEVELOPING', 'resume pending tasks')
        tasks = await schedule(tasks, () => true)
      }
    }
    const bad = tasks.filter(t => t.status !== 'COMPLETED' && !(t.kind === 'repair' && (t.status === 'FAILED' || t.status === 'CANCELLED' || t.status === 'BLOCKED')))
    if (bad.length) {
      const blocked = bad.some(t => t.status === 'BLOCKED')
      throw new StopRun(blocked ? 'BLOCKED' : 'NEEDS_HUMAN_REVIEW', `tasks not completed: ${bad.map(t => `${t.id}=${t.status}`).join(', ')}`)
    }
    if (!done('development')) await markStep('development')
    await integrate('initial', mode !== 'repair' && tasks.length > 1)
    let gate = await verification(mode, 'initial')
    gate = await repairLoop(gate, mode)
    if (!gate.passed) throw new StopRun('NEEDS_HUMAN_REVIEW', `quality gate failing: ${gate.failing_mandatory.join(', ')}`)
    return await finalize('COMPLETED', 'all mandatory gates passed for the current revision')
  } catch (e) {
    if (e instanceof StopRun && (e.status === 'NEEDS_HUMAN_REVIEW' || e.status === 'BLOCKED') && CTX && counters.agent_calls < CFG.max_agent_calls) {
      const advice = await specialist('orchestrator', header('RECOVERY', P().project, `${P().run_dir}/artifacts/recovery.json`) + `\n
The run is stopping with ${e.status}: ${e.reason}
Read the latest gate in ${P().run_dir}/gates/, open issues in ${P().run_dir}/issues/ and tasks in ${P().run_dir}/tasks.json. Recommend the smallest recovery (re-scope, contract change, environment fix, human decision). Return {"status": "COMPLETED", "summary": "<recommendation, max 3 sentences>"}.`, { label: 'recovery-analysis' })
      if (advice && advice.summary) e.reason = `${e.reason} | recovery advice: ${advice.summary}`
    }
    if (e instanceof StopRun) {
      if (e.status === 'AWAITING_APPROVAL') {
        return { run_id: DP.run_id, final_status: 'AWAITING_APPROVAL', reason: e.reason, next: 'Answer/approve, then run /devpipeline:resume-project' }
      }
      try { return await finalize(STOP_STATES.indexOf(e.status) >= 0 || e.status === 'COMPLETED' ? e.status : 'FAILED', e.reason) } catch (e2) {
        return { run_id: DP.run_id, final_status: e.status, reason: e.reason, finalize_error: String(e2 && e2.message) }
      }
    }
    const msg = String((e && e.message) || e)
    try { return await finalize('FAILED', `unexpected workflow error: ${msg}`) } catch (e2) {
      return { run_id: DP.run_id, final_status: 'FAILED', reason: msg, finalize_error: String(e2 && e2.message) }
    }
  }
}
