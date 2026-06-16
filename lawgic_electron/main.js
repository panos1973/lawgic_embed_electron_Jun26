'use strict';
const { app, BrowserWindow, ipcMain, dialog, safeStorage, shell } = require('electron');
const { spawn } = require('child_process');
const readline = require('readline');
const path = require('path');
const fs = require('fs');

// ---- settings (secrets encrypted at rest via OS keychain) ----
const SETTINGS_PATH = () => path.join(app.getPath('userData'), 'settings.json');
const SECRET_FIELDS = ['weaviateApiKey', 'voyageApiKey', 'diKey', 'anthropicKey', 'deepseekKey', 'geminiKey'];

function defaults() {
  return {
    pythonPath: process.platform === 'win32' ? 'python' : 'python3',
    coreDir: app.isPackaged
      ? path.join(process.resourcesPath, 'lawgic_pipeline')
      : path.join(__dirname, '..', 'lawgic_pipeline'),
    // Frozen PyInstaller core (dist/lawgic-core/), shipped via extraResources.
    // When present the app runs it directly and needs no Python install;
    // otherwise it falls back to `pythonPath cli.py` (the dev path).
    frozenDir: app.isPackaged
      ? path.join(process.resourcesPath, 'lawgic-core')
      : path.join(__dirname, '..', 'lawgic_pipeline', 'dist', 'lawgic-core'),
    weaviateUrl: 'https://dxyeak9tnm4gp8raeh1g.c0.europe-west3.gcp.weaviate.cloud',
    weaviateApiKey: '', voyageApiKey: '', diEndpoint: '', diKey: '',
    anthropicKey: '', deepseekKey: '', geminiKey: '', jurisdiction: 'gr',
    llmProvider: 'anthropic', llmModel: 'claude-haiku-4-5', llmThinking: false,
    amendExtractor: 'deterministic',
    stateDb: path.join(app.getPath('userData'), 'lawgic_state.db'),
  };
}

function loadSettings() {
  let s = defaults();
  try {
    const raw = JSON.parse(fs.readFileSync(SETTINGS_PATH(), 'utf-8'));
    s = Object.assign(s, raw);
    for (const f of SECRET_FIELDS) {
      if (typeof s[f] === 'string' && s[f].startsWith('enc:') && safeStorage.isEncryptionAvailable()) {
        s[f] = safeStorage.decryptString(Buffer.from(s[f].slice(4), 'base64'));
      }
    }
  } catch (_) { /* first run */ }
  return s;
}

function saveSettings(incoming) {
  const s = Object.assign(defaults(), incoming);
  const out = Object.assign({}, s);
  if (safeStorage.isEncryptionAvailable()) {
    for (const f of SECRET_FIELDS) {
      if (s[f]) out[f] = 'enc:' + safeStorage.encryptString(s[f]).toString('base64');
    }
  }
  fs.mkdirSync(path.dirname(SETTINGS_PATH()), { recursive: true });
  fs.writeFileSync(SETTINGS_PATH(), JSON.stringify(out, null, 2));
  return true;
}

function childEnv(s) {
  return Object.assign({}, process.env, {
    WEAVIATE_URL: s.weaviateUrl, WEAVIATE_API_KEY: s.weaviateApiKey,
    VOYAGE_API_KEY: s.voyageApiKey, DI_ENDPOINT: s.diEndpoint, DI_KEY: s.diKey,
    ANTHROPIC_API_KEY: s.anthropicKey, JURISDICTION: s.jurisdiction,
    DEEPSEEK_API_KEY: s.deepseekKey, GEMINI_API_KEY: s.geminiKey,
    LLM_PROVIDER: s.llmProvider, LLM_MODEL: s.llmModel,
    LLM_THINKING: s.llmThinking ? 'on' : 'off',
    AMEND_EXTRACTOR: s.amendExtractor || 'deterministic',
    STATE_DB: s.stateDb, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
    APP_VERSION: app.getVersion(),
  });
}

// ---- core invocation (frozen binary if present, else python cli.py) ----
let activeChild = null;

// Resolve the frozen core binary for this platform, or null if not bundled.
function frozenBinary(s) {
  const exe = process.platform === 'win32' ? 'lawgic-core.exe' : 'lawgic-core';
  const p = path.join(s.frozenDir, exe);
  try { return fs.existsSync(p) ? p : null; } catch (_) { return null; }
}

function spawnCore(args) {
  const s = loadSettings();
  const frozen = frozenBinary(s);
  if (frozen) {
    // Frozen onedir core: invoke the binary directly, cwd at its own folder so
    // PyInstaller resolves its bundled libs and our package data.
    return spawn(frozen, ['--json', ...args], {
      cwd: s.frozenDir, env: childEnv(s),
    });
  }
  const script = path.join(s.coreDir, 'cli.py');
  return spawn(s.pythonPath, [script, '--json', ...args], {
    cwd: s.coreDir, env: childEnv(s),
  });
}

// streaming command (ingest / retry): forwards each JSON line to the renderer
function runStreaming(win, args) {
  if (activeChild) return;
  const child = spawnCore(args);
  activeChild = child;
  const rl = readline.createInterface({ input: child.stdout });
  rl.on('line', (line) => {
    line = line.trim();
    if (!line) return;
    try { win.webContents.send('pipeline:event', JSON.parse(line)); }
    catch (_) { win.webContents.send('pipeline:event', { type: 'log', msg: line }); }
  });
  let err = '';
  child.stderr.on('data', (d) => { err += d.toString(); });
  child.on('close', (code) => {
    activeChild = null;
    win.webContents.send('pipeline:done', { code, error: code === 0 ? '' : err });
  });
  child.on('error', (e) => {
    activeChild = null;
    win.webContents.send('pipeline:done', { code: -1, error: e.message });
  });
}

// one-shot command (status / review): resolves the relevant JSON line
function runOneShot(args, wantType) {
  return new Promise((resolve) => {
    const child = spawnCore(args);
    let out = '', err = '';
    child.stdout.on('data', (d) => { out += d.toString(); });
    child.stderr.on('data', (d) => { err += d.toString(); });
    child.on('close', () => {
      let result = null;
      for (const line of out.split('\n')) {
        const t = line.trim(); if (!t) continue;
        try { const o = JSON.parse(t); if (o.type === wantType) result = o; } catch (_) {}
      }
      resolve(result || { error: err || 'no output' });
    });
    child.on('error', (e) => resolve({ error: e.message }));
  });
}

// ---- IPC ----
function registerIpc(win) {
  ipcMain.handle('dialog:selectFolder', async () => {
    const r = await dialog.showOpenDialog(win, { properties: ['openDirectory'] });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle('pipeline:start', (_e, folder) => { runStreaming(win, ['ingest', folder]); return true; });
  ipcMain.handle('pipeline:retry', () => { runStreaming(win, ['retry']); return true; });
  ipcMain.on('pipeline:cancel', () => { if (activeChild) activeChild.kill(); });
  ipcMain.handle('pipeline:status', () => runOneShot(['status'], 'status'));
  ipcMain.handle('pipeline:review', () => runOneShot(['review'], 'review'));
  ipcMain.handle('state:reset', () => runOneShot(['reset-state'], 'reset_state'));
  ipcMain.handle('collections:list', () => runOneShot(['collections'], 'collections'));
  ipcMain.handle('collections:reset', (_e, key) => runOneShot(['reset', key], 'reset'));
  ipcMain.handle('laws:list', () => runOneShot(['laws'], 'laws'));
  ipcMain.handle('law:inspect', (_e, { collection, law }) =>
    runOneShot(['inspect', '--collection', collection, '--law', law], 'inspect'));
  ipcMain.handle('settings:get', () => {
    const s = loadSettings();
    return Object.assign(s, { encryptionAvailable: safeStorage.isEncryptionAvailable() });
  });
  ipcMain.handle('settings:save', (_e, obj) => saveSettings(obj));
  ipcMain.handle('app:version', () => app.getVersion());
  // the core writes lawgic.log next to the state DB; open that folder
  ipcMain.handle('logs:open', () => {
    const dir = path.dirname(loadSettings().stateDb);
    shell.openPath(dir);
    return path.join(dir, 'lawgic.log');
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100, height: 760, minWidth: 820, minHeight: 560,
    backgroundColor: '#0e0f12', title: `Lawgic · FEK Ingest · v${app.getVersion()}`,
    webPreferences: { preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  registerIpc(win);
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
