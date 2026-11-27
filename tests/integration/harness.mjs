// Deterministic workflow harness.
//
// Executes the REAL generated workflow files (workflows/*.js) inside a fake
// Claude Code workflow runtime: agent()/parallel()/pipeline()/phase()/log()/
// args/budget are provided here, Date.now()/Math.random()/argless Date throw
// exactly like the real runtime. `pipeline-operator` calls run the REAL
// deterministic helpers (python3 scripts/devpipeline.py ...); specialist agents
// are deterministic fakes (tests/integration/fakes.mjs). These are MOCKED
// orchestration tests — they prove control flow, state, gates and repair logic,
// not the quality of real Claude agents (see tests/smoke for the real runtime).
import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
const CLEAN_ENV = Object.fromEntries(Object.entries(globalThis.process.env).filter(([k]) => !k.startsWith('NODE_TEST')))
CLEAN_ENV.DEVPIPELINE_STATE_DIR = mkdtempSync(join(tmpdir(), 'dp-state-'))
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

export function loadWorkflow(file) {
  const text = readFileSync(join(ROOT, 'workflows', file), 'utf8')
  if (!text.startsWith('export const meta = {')) throw new Error(`${file}: meta must be first`)
  const end = text.indexOf('\n}\n')
  const metaSrc = text.slice('export const meta = '.length, end + 2)
  const meta = new Function(`return (${metaSrc})`)()
  const body = text.slice(end + 3)
  return { meta, body }
}

export function py(args, env = {}) {
  const r = spawnSync('python3', [join(ROOT, 'scripts', 'devpipeline.py'), ...args], {
    encoding: 'utf8', env: { ...CLEAN_ENV, DEVPIPELINE_OFFLINE: '1', ...env },
  })
  const line = (r.stdout || '').trim().split('\n').pop()
  return JSON.parse(line)
}

function runOperatorCommand(prompt) {
  const m = prompt.match(/<command>([\s\S]*?)<\/command>/)
  if (!m) throw new Error('operator prompt without <command>')
  const r = spawnSync('bash', ['-c', m[1]], { encoding: 'utf8', env: { ...CLEAN_ENV, DEVPIPELINE_OFFLINE: '1' }, maxBuffer: 64 * 1024 * 1024 })
  const lines = (r.stdout || '').trim().split('\n')
  let output
  try { output = JSON.parse(lines[lines.length - 1]) } catch { output = { ok: false, error: (r.stderr || r.stdout || '').slice(-500) } }
  return { exit_code: r.status ?? 1, output }
}

export class Killed extends Error {}

// fakes: { [agentName]: async (prompt, opts, info) => result | null }
export async function runWorkflow(file, args, fakes, options = {}) {
  const { meta, body } = loadWorkflow(file)
  const calls = []
  const logs = []
  const state = { killed: false, tamperOnce: options.tamperOnce || null, active: new Map(), maxConcurrent: {} }
  const RealDate = Date
  function FakeDate(...a) { if (!a.length) throw new Error('Date() is not available in workflows'); return new RealDate(...a) }
  FakeDate.now = () => { throw new Error('Date.now() is not available in workflows') }
  const FakeMath = Object.create(Math)
  FakeMath.random = () => { throw new Error('Math.random() is not available in workflows') }

  async function agent(prompt, opts = {}) {
    if (state.killed) throw new Killed('session killed')
    const name = (opts.agentType || '').replace(/^devpipeline:/, '')
    calls.push({ name, label: opts.label, phase: opts.phase, model: opts.model })
    if (name === 'pipeline-operator') {
      const res = runOperatorCommand(prompt)
      if (state.tamperOnce && prompt.includes(` ${state.tamperOnce} `)) {
        state.tamperOnce = null
        return { exit_code: res.exit_code, output: { ...res.output, injected: 'tampered' } }
      }
      return res
    }
    const fake = fakes[name]
    if (!fake) throw new Error(`no fake for agent ${name}`)
    const info = parsePrompt(prompt)
    if (options.killWhen && options.killWhen(name, info, calls)) { state.killed = true; throw new Killed('killed during ' + info.taskId) }
    const key = info.taskId || name
    state.active.set(key, (state.active.get(key) || 0) + 1)
    const snapshot = [...state.active.keys()]
    for (const k of snapshot) state.maxConcurrent[k] = Math.max(state.maxConcurrent[k] || 0, snapshot.length)
    options.onConcurrent && options.onConcurrent(snapshot)
    await new Promise(r => setTimeout(r, 5))
    try {
      return await fake(prompt, opts, info)
    } finally {
      const n = state.active.get(key) - 1
      if (n <= 0) state.active.delete(key); else state.active.set(key, n)
    }
  }
  async function parallel(thunks) {
    return Promise.all(thunks.map(t => Promise.resolve().then(t).catch(() => null)))
  }
  async function pipeline(items, ...stages) {
    return Promise.all(items.map(async (item, i) => {
      let v = item
      try { for (const s of stages) v = await s(v, item, i) } catch { return null }
      return v
    }))
  }
  const fn = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'workflow', 'Date', 'Math', body)
  const budget = { total: null, spent: () => 0, remaining: () => Infinity }
  const result = await fn(agent, parallel, pipeline, () => {}, m => logs.push(m), args, budget, async () => { throw new Error('nested workflows not used') }, FakeDate, FakeMath)
  return { meta, result, calls, logs, state }
}

export function parsePrompt(prompt) {
  const get = re => { const m = prompt.match(re); return m ? m[1].trim() : null }
  return {
    taskId: get(/^Task id: (\S+)/m),
    cwd: get(/^Working directory: (\S+)/m),
    resultPath: get(/^Result path: (\S+)/m),
    runDir: get(/^Run directory \(logs, screenshots, artifacts\): (\S+)/m),
    fingerprint: get(/"source_fingerprint": "([^"]+)"/),
    runId: get(/^DevPipeline run (\S+)/m),
    project: get(/— project (\S+)/),
    prompt,
  }
}
