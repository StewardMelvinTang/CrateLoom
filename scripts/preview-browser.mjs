// Run the interface in an ordinary browser against a real engine process.
//
// The desktop shell normally owns the engine. For interface work it is faster
// to load the same frontend in a browser, so this starts Vite, spawns the
// Python engine, and injects the two calls the frontend expects from Tauri.
//
// CRATELOOM_DATA is required so a preview never opens the owner's library by
// accident:
//
//   CRATELOOM_DATA=/path/to/disposable-library node scripts/preview-browser.mjs
//
// Native dialogs are unavailable here (file pickers resolve as cancelled).
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const data = process.env.CRATELOOM_DATA;
if (!data) {
  console.error('Set CRATELOOM_DATA to a disposable library folder.');
  process.exit(2);
}
const port = Number(process.env.CRATELOOM_PREVIEW_PORT || 1430);

const env = { ...process.env, PYTHONPATH: path.join(root, 'engine'), PYTHONDONTWRITEBYTECODE: '1' };
env.PATH = [env.PATH, '/opt/homebrew/bin', '/usr/local/bin'].filter(Boolean).join(path.delimiter);
for (const [key, folder] of [['CRATELOOM_ANALYSIS_PYTHON', 'analysis-env'], ['CRATELOOM_STRUCTURE_PYTHON', 'structure-env'],
  ['CRATELOOM_METADATA_PYTHON', 'metadata-env'], ['CRATELOOM_SHAZAM_PYTHON', 'shazam-env']]) {
  const python = path.join(os.homedir(), 'Library/Caches/CrateLoom', folder, 'bin/python');
  if (!env[key] && fs.existsSync(python)) env[key] = python;
}

const engine = spawn(env.CRATELOOM_PYTHON || 'python3', ['-m', 'crateloom'], { env, stdio: ['pipe', 'pipe', 'inherit'] });
const lines = readline.createInterface({ input: engine.stdout });
const waiting = [];
lines.on('line', line => waiting.shift()?.(line));
engine.on('exit', code => { console.error(`Engine exited (${code}).`); process.exit(1); });

// The engine answers one request at a time, exactly as the desktop bridge uses it.
let chain = Promise.resolve();
const allowed = new Set();
function remember(value) {
  if (typeof value === 'string') { if (path.isAbsolute(value)) allowed.add(value); }
  else if (Array.isArray(value)) value.forEach(remember);
  else if (value && typeof value === 'object') Object.values(value).forEach(remember);
}
function call(method, params) {
  const next = chain.then(() => new Promise((resolve, reject) => {
    waiting.push(line => {
      const reply = JSON.parse(line);
      if (reply.error) reject(new Error(reply.error.message));
      else { remember(reply.result); resolve(reply.result); }
    });
    engine.stdin.write(JSON.stringify({ protocol_version: 1, request_id: 'preview', method, params }) + '\n');
  }));
  chain = next.catch(() => {});
  return next;
}

const types = { '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.aac': 'audio/aac', '.wav': 'audio/wav', '.flac': 'audio/flac',
  '.ogg': 'audio/ogg', '.opus': 'audio/ogg', '.aiff': 'audio/aiff', '.aif': 'audio/aiff', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg' };
function sniff(file) {
  const head = Buffer.alloc(4);
  const fd = fs.openSync(file, 'r'); fs.readSync(fd, head, 0, 4, 0); fs.closeSync(fd);
  if (head[0] === 0x89 && head[1] === 0x50) return 'image/png';
  if (head[0] === 0xff && head[1] === 0xd8) return 'image/jpeg';
  return 'application/octet-stream';
}
function serveFile(request, response) {
  const file = new URL(request.url, 'http://localhost').searchParams.get('path') || '';
  // Mirror the desktop asset scope: only paths the engine itself returned.
  if (!allowed.has(file) || !fs.existsSync(file)) { response.statusCode = 404; response.end(); return; }
  const size = fs.statSync(file).size;
  const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.range || '');
  response.setHeader('Content-Type', types[path.extname(file).toLowerCase()] || sniff(file));
  response.setHeader('Accept-Ranges', 'bytes');
  if (range && (range[1] || range[2])) {
    const start = range[1] ? Number(range[1]) : Math.max(0, size - Number(range[2]));
    const end = range[1] && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
    response.statusCode = 206;
    response.setHeader('Content-Range', `bytes ${start}-${end}/${size}`);
    response.setHeader('Content-Length', end - start + 1);
    fs.createReadStream(file, { start, end }).pipe(response);
  } else {
    response.setHeader('Content-Length', size);
    fs.createReadStream(file).pipe(response);
  }
}

const shim = `window.__TAURI_INTERNALS__={convertFileSrc:p=>'/__asset?path='+encodeURIComponent(p),invoke:async(cmd,args)=>{
  if(cmd==='open_reference'){window.open(args.url,'_blank','noopener');return null;}
  if(cmd!=='engine_call')return null;
  const reply=await fetch('/__engine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});
  const body=await reply.json();
  if(!reply.ok)throw body.error;
  return body.result;
}};`;

const bridge = {
  name: 'crateloom-browser-bridge',
  configureServer(server) {
    server.middlewares.use('/__engine', (request, response) => {
      let body = '';
      request.on('data', chunk => { body += chunk; });
      request.on('end', async () => {
        response.setHeader('Content-Type', 'application/json');
        try {
          const { method, params } = JSON.parse(body);
          response.end(JSON.stringify({ result: await call(method, params ?? {}) }));
        } catch (error) {
          response.statusCode = 500;
          response.end(JSON.stringify({ error: String(error.message || error) }));
        }
      });
    });
    server.middlewares.use('/__asset', serveFile);
  },
  transformIndexHtml: () => [{ tag: 'script', children: shim, injectTo: 'head-prepend' }],
};

await call('handshake', {});
const server = await createServer({
  configFile: path.join(root, 'apps/desktop/vite.config.ts'),
  plugins: [bridge],
  server: { port, strictPort: true, host: '127.0.0.1' },
});
await server.listen();
console.log(`CrateLoom preview: http://127.0.0.1:${port}  (library: ${data})`);

const stop = () => { engine.stdin.end(); server.close().finally(() => process.exit(0)); };
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
