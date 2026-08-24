import { spawn, spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import process from 'node:process';

const root = process.cwd();
const bootstrapPython = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const prepared = spawnSync(bootstrapPython, ['scripts/bootstrap.py'], {
  cwd: root,
  stdio: 'inherit'
});
if (prepared.status !== 0) process.exit(prepared.status ?? 1);

const runtime = JSON.parse(readFileSync(join(root, '.dev/runtime-env.json'), 'utf8'));
const backendEnv = {
  ...process.env,
  BREWIE_APP_ROOT: root,
  BREWIE_AVR_ENABLED: '0',
  EDITOR_HOST: '127.0.0.1',
  EDITOR_PORT: '8081',
  EDITOR_DEBUG: '0',
  PROCEDURES_DIR: runtime.procedures,
  SCHEMA_PATH: join(runtime.program_release, 'schemas/procedure.schema.json'),
  GRAPH_SCHEMA_PATH: join(runtime.program_release, 'schemas/workflow.schema.json'),
  RECIPE_SCHEMA_PATH: join(root, 'services/runtime/recipe.schema.json'),
  BUNDLED_RECIPES_DIR: join(root, 'fixtures/recipes'),
  RECIPES_DIR: runtime.recipes,
  BREWIE_HARDWARE_REGISTRY: join(root, 'services/runtime/hardware-device-registry.yml')
};

const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) {
    if (!child.killed) child.kill('SIGTERM');
  }
  setTimeout(() => process.exit(code), 250);
}
process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));

const backend = spawn(runtime.python, ['services/runtime/editor_backend.py'], {
  cwd: root,
  env: backendEnv,
  stdio: 'inherit'
});
children.push(backend);
backend.on('exit', (code) => {
  if (!stopping) stop(code ?? 1);
});

async function waitForBackend() {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch('http://127.0.0.1:8081/api/health');
      if (response.ok) return;
    } catch (_) {
      // The backend is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('Runtime API did not become ready within 30 seconds');
}

try {
  await waitForBackend();
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const web = spawn(
    npm,
    ['run', 'dev', '--workspace', '@aircable/brewienext-web', '--', '--host', '127.0.0.1'],
    { cwd: root, stdio: 'inherit' }
  );
  children.push(web);
  web.on('exit', (code) => {
    if (!stopping) stop(code ?? 1);
  });
} catch (error) {
  console.error(error.message);
  stop(1);
}
