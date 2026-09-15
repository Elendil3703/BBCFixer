/**
 * ESM loader hook of the boundary-call recorder (differential execution, Section 3.2.1).
 *
 * Why it is needed: js_boundary_trace.js hooks by rewriting Module._load and only covers CommonJS.
 * ESM imports do not go through Module._load, so the recorder cannot attach, and the output of an
 * unattached recorder looks exactly like "no behavioral difference between the two versions". The
 * trigger is a downstream project in ESM whose library ships a real ESM entry in the new version: if
 * the old version only has CommonJS output, Node still takes the interop path and gets hooked, so only
 * the broken-state side is lost and the comparison degrades into spurious "reached in the intact state only" divergences.
 *
 * Approach: in the load hook only, replace the library entry module directly imported by the
 * downstream project with a shim source. The shim imports the real module from the same URL with a
 * marker, so the real module is instantiated only once. Wrapping and recording always call back into
 * __internals.esmAttach of js_boundary_trace.js, records go into the same ST.records and are written
 * by the same dump(), so the fields and normalization of trace.jsonl match the CommonJS side by construction.
 *
 * The record path naming must match the CommonJS side, otherwise the two sides cannot be matched:
 *   under CommonJS `import chokidar from 'chokidar'` is recorded as `chokidar.watch`,
 *   so under ESM the default export uses the package name itself as its path, not `chokidar.default.watch`.
 *
 * No dynamic import inside the hook: since Node 18.19 the loader runs on a separate thread, and that would evaluate the module twice.
 *
 * Environment variables (shared with js_boundary_trace.js):
 *   ALGO_TRACE_PKGS / ALGO_TRACE_DIR / ALGO_TRACE_ROOT / ALGO_TRACE_MODE / ALGO_TRACE_SCOPE
 *   ALGO_TRACE_SELF   absolute path of the recorder itself, set by js_esm_register.mjs
 *   ALGO_TRACE_ESM    set to 0 to disable this hook entirely (for comparison runs)
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

function envStr(k, d) { const v = process.env[k]; return (v === undefined || v === '') ? d : v; }

const PKGS = envStr('ALGO_TRACE_PKGS', '').split(',').map(s => s.trim()).filter(Boolean);
const DIR = envStr('ALGO_TRACE_DIR', '');
const ROOT = envStr('ALGO_TRACE_ROOT', process.cwd());
const MODE = envStr('ALGO_TRACE_MODE', 'active');
const SCOPE = envStr('ALGO_TRACE_SCOPE', 'project');
// The recorder and this hook live in the same directory (mounted at /algo in the container); take it from there, no environment variable needed.
const SELF = envStr('ALGO_TRACE_SELF',
  path.join(path.dirname(fileURLToPath(import.meta.url)), 'js_boundary_trace.js'));
const ENABLED = envStr('ALGO_TRACE_ESM', '1') !== '0';

const ON = ENABLED && PKGS.length > 0 && DIR !== '' && MODE === 'active' && SELF !== '';

// Distinguishes the re-entrant import of the real module. Uses text that never appears in a normal path, to avoid false matches.
const MARK = 'algo_esm_orig=1';

// pkg -> real directory of the package under node_modules (with trailing separator)
const TARGET_DIRS = new Map();
for (const p of PKGS) {
  const d = path.join(ROOT, 'node_modules', p);
  let real = d;
  try { real = fs.realpathSync(d); } catch (e) { real = d; }
  TARGET_DIRS.set(p, real + path.sep);
}

// Entries recognized at the resolve stage and due to be wrapped: URL without query -> pkg
const wrapTargets = new Map();
// Diagnostic counters, written into the dump directory so that whether the hook worked can be traced afterwards
const stat = { resolved: 0, shimmed: 0, skippedCjs: 0, skippedNested: 0, badNames: 0 };

function urlToPath(u) {
  try { return fileURLToPath(u.split('?')[0]); } catch (e) { return ''; }
}

/** Whether a bare specifier points at one of the libraries. Both `chokidar` and `chokidar/xxx` count. */
function bareTarget(spec) {
  if (!spec || spec[0] === '.' || spec[0] === '/' || spec.includes('://')) return null;
  for (const p of PKGS) {
    if (spec === p || spec.startsWith(p + '/')) return p;
  }
  return null;
}

function underNM(u) {
  if (!u) return false;
  const f = urlToPath(u);
  if (!f) return false;
  return f.includes(path.sep + 'node_modules' + path.sep);
}

function inPkgDir(u, pkg) {
  const f = urlToPath(u);
  const d = TARGET_DIRS.get(pkg);
  return !!(f && d && f.indexOf(d) === 0);
}

export async function resolve(specifier, context, nextResolve) {
  if (!ON) return nextResolve(specifier, context);
  // The shim importing the real module: pass through as is; the format is decided by load.
  if (specifier.includes(MARK)) {
    return { url: specifier, shortCircuit: true };
  }
  const r = await nextResolve(specifier, context);
  try {
    const pkg = bareTarget(specifier);
    if (pkg) {
      // Same rule as SCOPE=project on the CommonJS side: only wrap the library where the downstream
      // project's own code imports it; intermediate dependencies (packages in node_modules importing the library) are not wrapped.
      if (SCOPE === 'project' && underNM(context.parentURL)) {
        stat.skippedNested++;
      } else if (inPkgDir(r.url, pkg)) {
        stat.resolved++;
        wrapTargets.set(r.url.split('?')[0], pkg);
      }
    }
  } catch (e) { /* the recorder must never affect the program under test */ }
  return r;
}

export async function load(url, context, nextLoad) {
  if (!ON) return nextLoad(url, context);
  // The real module: strip the marker and load as is.
  if (url.includes(MARK)) {
    return nextLoad(url.split('?')[0], context);
  }
  const pkg = wrapTargets.get(url);
  if (!pkg) return nextLoad(url, context);

  const r = await nextLoad(url, context);
  // Only real ESM needs this hook. CommonJS entries are still hooked on the Module._load side; wrapping again would record twice.
  if (r.format !== 'module') { stat.skippedCjs++; return r; }

  let src = '';
  try {
    src = typeof r.source === 'string' ? r.source : Buffer.from(r.source).toString('utf8');
  } catch (e) { return r; }

  const names = exportNames(src);
  const shim = buildShim(url + (url.includes('?') ? '&' : '?') + MARK, pkg, names);
  stat.shimmed++;
  dumpStat();
  return { format: 'module', source: shim, shortCircuit: true };
}

// ---------------------------------------------------------------- export name extraction
// The export names must be written statically into the shim and cannot be generated at run time, so
// they are extracted from the source. Comments and strings are blanked first, so that the word export
// inside a comment or string is not taken for a real export. Missed names are covered by the trailing
// `export * from <real module>`; wrong names are checked explicitly in the shim and raise an error, never silently shadowing a real export as undefined.
const RESERVED = new Set(['default', 'do', 'if', 'in', 'for', 'let', 'new', 'try', 'var',
  'case', 'else', 'enum', 'null', 'this', 'true', 'void', 'with', 'break', 'catch', 'class',
  'const', 'false', 'super', 'throw', 'while', 'yield', 'delete', 'export', 'import',
  'return', 'switch', 'typeof', 'function', 'continue', 'debugger', 'instanceof']);

function scrub(src) {
  // Replace comments and string literals with blanks of equal length; the line structure is preserved.
  let out = '';
  let i = 0;
  const n = src.length;
  let state = 0; // 0 code 1 line comment 2 block comment 3 single quote 4 double quote 5 backtick
  while (i < n) {
    const c = src[i];
    const d = src[i + 1];
    if (state === 0) {
      if (c === '/' && d === '/') { state = 1; out += '  '; i += 2; continue; }
      if (c === '/' && d === '*') { state = 2; out += '  '; i += 2; continue; }
      if (c === "'") { state = 3; out += ' '; i++; continue; }
      if (c === '"') { state = 4; out += ' '; i++; continue; }
      if (c === '`') { state = 5; out += ' '; i++; continue; }
      out += c; i++; continue;
    }
    if (state === 1) {
      if (c === '\n') { state = 0; out += '\n'; i++; continue; }
      out += ' '; i++; continue;
    }
    if (state === 2) {
      if (c === '*' && d === '/') { state = 0; out += '  '; i += 2; continue; }
      out += (c === '\n' ? '\n' : ' '); i++; continue;
    }
    // inside a string
    if (c === '\\') { out += '  '; i += 2; continue; }
    if ((state === 3 && c === "'") || (state === 4 && c === '"') || (state === 5 && c === '`')) {
      state = 0; out += ' '; i++; continue;
    }
    out += (c === '\n' ? '\n' : ' '); i++;
  }
  return out;
}

function exportNames(srcRaw) {
  const src = scrub(srcRaw);
  const named = [];
  let hasDefault = false;
  const add = (n) => {
    if (!n) return;
    if (n === 'default') { hasDefault = true; return; }
    if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(n)) return;
    if (RESERVED.has(n)) return;
    if (!named.includes(n)) named.push(n);
  };

  if (/(^|[^\w$])export\s+default[\s({[]/.test(src)) hasDefault = true;

  let m;
  const reDecl = /(^|[^\w$])export\s+(?:async\s+)?(?:function\s*\*?|class|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)/g;
  while ((m = reDecl.exec(src)) !== null) add(m[2]);

  const reBrace = /(^|[^\w$])export\s*\{([^}]*)\}/g;
  while ((m = reBrace.exec(src)) !== null) {
    for (const part of m[2].split(',')) {
      const t = part.trim();
      if (!t) continue;
      const as = t.split(/\s+as\s+/);
      add((as.length > 1 ? as[as.length - 1] : as[0]).trim());
    }
  }
  return { named, hasDefault };
}

function buildShim(origUrl, pkg, names) {
  const j = JSON.stringify;
  const L = [];
  L.push('// Recording shim generated by js_esm_trace_loader.mjs; never written to disk, the workspace under test is not modified.');
  L.push(`import * as __algo_ns from ${j(origUrl)};`);
  L.push(`import { createRequire as __algo_cr } from 'node:module';`);
  L.push(`const __algo_t = __algo_cr(import.meta.url)(${j(SELF)});`);
  L.push(`const __algo_names = ${j(names.named)};`);
  // A wrongly extracted export name must fail loudly, never silently shadow as undefined and produce wrong evidence.
  L.push('const __algo_miss = __algo_names.filter(function (n) { return !(n in __algo_ns); });');
  L.push('if (__algo_miss.length) { throw new Error("[algo-esm-trace] export name does not exist: " + __algo_miss.join(",")); }');
  L.push(`const __algo_w = __algo_t.__internals.esmAttach(${j(pkg)}, __algo_ns, __algo_names);`);
  if (names.hasDefault) L.push('export default __algo_w.default;');
  for (const n of names.named) L.push(`export const ${n} = __algo_w[${j(n)}];`);
  // Missed names are covered by this line. Explicitly exported names take precedence, so there is no conflict.
  L.push(`export * from ${j(origUrl)};`);
  return L.join('\n') + '\n';
}

function dumpStat() {
  try {
    fs.writeFileSync(path.join(DIR, 'esm-loader.json'), JSON.stringify({
      pkgs: PKGS, root: ROOT, scope: SCOPE,
      targets: Array.from(wrapTargets.entries()).map(function (e) { return { url: e[0], pkg: e[1] }; }),
      stat: stat
    }, null, 2) + '\n');
  } catch (e) { }
}
