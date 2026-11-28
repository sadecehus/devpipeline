#!/usr/bin/env node
// Cross-platform launcher for npx-based stdio MCP servers (.mcp.json → "command": "node").
//
// On native Windows Claude Code cannot spawn `npx` directly: npx is npx.cmd, a batch file, and the server dies with
// "Connection closed". Running npm's own npx-cli.js with the current node avoids the shell entirely and works the
// same on macOS/Linux. stdio is inherited, so the MCP protocol flows straight through.
//
//   node mcp_npx.mjs <package@version> [server args...]
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'

const args = ['-y', ...process.argv.slice(2)]
const nodeDir = dirname(process.execPath)
const candidates = [
  join(nodeDir, 'node_modules', 'npm', 'bin', 'npx-cli.js'),              // Windows installer, nvm-windows, Volta
  join(nodeDir, '..', 'lib', 'node_modules', 'npm', 'bin', 'npx-cli.js'), // macOS/Linux (pkg, Homebrew, nvm)
]
const cli = candidates.find(existsSync)

let child
if (cli) {
  child = spawn(process.execPath, [cli, ...args], { stdio: 'inherit' })
} else if (process.platform === 'win32') {
  // last resort: through cmd.exe with every argument quoted
  const quoted = args.map(a => (/[\s"&|<>^]/.test(a) ? `"${a.replace(/"/g, '""')}"` : a))
  child = spawn('npx.cmd', quoted, { stdio: 'inherit', shell: true })
} else {
  child = spawn('npx', args, { stdio: 'inherit' })
}
child.on('error', err => {
  process.stderr.write(`devpipeline mcp launcher: cannot start npx (${err.message}). Is Node.js with npm installed?\n`)
  process.exit(127)
})
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig))
child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)))
