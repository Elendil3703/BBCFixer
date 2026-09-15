'use strict';
/**
 * Boundary-call recorder (differential execution, Section 3.2.1; JavaScript / CommonJS ecosystem).
 *
 * Counterpart of py_boundary_trace.py: during a test run, record every boundary call from the
 * downstream project into the upgraded library (function path, arguments, return value or
 * thrown exception) as JSONL; one trace is taken in each of the intact and broken states, and
 * behavior_diff.py compares them mechanically to locate the first call whose return value differs.
 *
 * Injection (not a single byte of the downstream project or its dependency tree is changed):
 *     NODE_OPTIONS="--require /path/js_boundary_trace.js" \
 *     ALGO_TRACE_PKGS=lodash ALGO_TRACE_DIR=/algo-out/old npm test
 * This file lives outside the project and its node_modules; it is preloaded with --require and hooks Module._load.
 *
 * Environment variables:
 *   ALGO_TRACE_PKGS    comma-separated upstream package names (required; empty disables the recorder entirely)
 *   ALGO_TRACE_DIR     output directory of the per-process records (required; empty disables)
 *   ALGO_TRACE_ROOT    project root, default process.cwd()
 *   ALGO_TRACE_MAX     maximum number of boundary calls to record (default 4000; beyond it recording stops and truncated is set)
 *   ALGO_TRACE_STR     length cap of a single string value (default 160)
 *   ALGO_TRACE_ITEMS   maximum number of container elements expanded (default 12)
 *   ALGO_TRACE_DEPTH   serialization depth cap (default 3)
 *   ALGO_TRACE_WRAPDEPTH  member wrapping depth cap (default 3)
 *   ALGO_TRACE_RETWRAP    how many levels of returned functions are wrapped again (default 1; 0 disables)
 *   ALGO_TRACE_SCOPE   project (default: only wrap the library where the downstream project's own code requires it) | all
 *   ALGO_TRACE_MODE    active (default) | passive (only record require events; no wrapping, no call records)
 *   ALGO_TRACE_PROMISE 1 (default: observe the settled value of returned promises) | 0
 *   ALGO_TRACE_NONORM  1 = disable normalization (for mutation checks only; never in a real run)
 *
 * What is recorded: only "boundary calls", i.e. the first hop from downstream or test code into the
 * upstream package; calls inside the upstream package are not recorded. The criterion is whether the
 * first stack frame not belonging to this file lies inside the package directory.
 *
 * Known blind spots (reported explicitly, never silently degraded):
 *   - This file only covers CommonJS. ESM imports do not go through Module._load and cannot be hooked
 *     here; js_esm_trace_loader.mjs in the same directory covers them via module.register and calls
 *     back into __internals.esmAttach of this file, so both channels share the same record format
 *     and normalization. When a .mjs file or type=module is loaded, esm_seen is still set in _meta
 *     for the caller to check. If the ESM hook is disabled with ALGO_TRACE_ESM=0, an ESM downstream
 *     project is unhookable again; attached is then false and the caller must treat it as a failure,
 *     never as "no behavioral difference between the two versions".
 *   - Only the top-level node_modules/<pkg> is hooked. Nested old copies deliberately kept by the
 *     harness (identical in both states) are out of scope.
 *   - Observing a promise's settled value requires attaching .then, which marks the promise as handled
 *     and may suppress "possibly unhandled rejection" warnings. Set ALGO_TRACE_PROMISE=0 when zero interference is required.
 */

var fs = require('fs');
var path = require('path');
var Module = require('module');

// ------------------------------------------------------------------ config
function envStr(k, d) { var v = process.env[k]; return (v === undefined || v === '') ? d : v; }
function envInt(k, d) { var v = parseInt(process.env[k], 10); return isNaN(v) ? d : v; }

var CFG = {
  PKGS: envStr('ALGO_TRACE_PKGS', '').split(',').map(function (s) { return s.trim(); }).filter(Boolean),
  DIR: envStr('ALGO_TRACE_DIR', ''),
  ROOT: envStr('ALGO_TRACE_ROOT', process.cwd()),
  MAX: envInt('ALGO_TRACE_MAX', 4000),
  STRMAX: envInt('ALGO_TRACE_STR', 160),
  ITEMS: envInt('ALGO_TRACE_ITEMS', 12),
  DEPTH: envInt('ALGO_TRACE_DEPTH', 3),
  WRAPDEPTH: envInt('ALGO_TRACE_WRAPDEPTH', 3),
  RETWRAP: envInt('ALGO_TRACE_RETWRAP', 1),
  SCOPE: envStr('ALGO_TRACE_SCOPE', 'project'),
  MODE: envStr('ALGO_TRACE_MODE', 'active'),
  PROMISE: envInt('ALGO_TRACE_PROMISE', 1),
  VALMAX: envInt('ALGO_TRACE_VALMAX', 400),
  NONORM: envInt('ALGO_TRACE_NONORM', 0),
  CBARGS: envInt('ALGO_TRACE_CBARGS', 0)
};

// ------------------------------------------------------------------ state
var ST = null;

function freshState() {
  return {
    records: [],
    seq: 0,
    truncated: false,
    dumped: false,
    installed: false,
    ordinal: 0,
    targetDirs: {},        // pkg -> absolute directory (with trailing separator)
    missing: [],           // packages declared but not found in node_modules
    hooked: {},            // packages actually wrapped
    snapshots: {},         // pkg -> { prop: original value }, for the upstream namespace integrity check
    esmSeen: false,
    norm: {},              // hit counts of the normalization rules
    reqStats: { total: 0, internal: 0, intermediate: 0, hooked: 0 },
    wrapCache: new WeakMap(),
    unwrapMap: new WeakMap(),
    origLoad: null
  };
}

function bump(k) { ST.norm[k] = (ST.norm[k] || 0) + 1; }

// ------------------------------------------------------------------ normalization
// One goal only: running the same code at the same version twice must give byte-identical output.
// Anything that varies between runs (absolute paths, timestamps, UUIDs, ports) is replaced by a
// fixed marker and counted in _meta, so that how much was replaced can be audited afterwards:
// normalization itself may hide real differences, so it must be visible.
var ISO_RE = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})/g;
var UUID_RE = /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g;
var PORT_RE = /(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):\d{2,5}/g;
var TMP_RE = /\/(?:tmp|var\/folders|var\/tmp)\/[A-Za-z0-9_.\-\/]*/g;
// Bare epoch milliseconds inside strings (e.g. a return value spliced into text). Only integers of
// exactly 13 digits, not adjacent to other digits, in the 2001-09 to 2096-10 range; hits become <EPOCH_MS>.
// A real source of nondeterminism found by the self-check on node:16: normNum only handles numeric
// arguments, not a value embedded in a string.
var EPOCHS_RE = /(^|[^0-9])([0-9]{13})(?![0-9])/g;

function normStr(s) {
  if (typeof s !== 'string' || s.length === 0) return s;
  if (CFG.NONORM) return s;
  var before = s;
  var nm = path.join(CFG.ROOT, 'node_modules') + path.sep;
  var rootpfx = CFG.ROOT + path.sep;
  var home = process.env.HOME ? process.env.HOME + path.sep : null;
  if (s.indexOf(nm) >= 0) s = s.split(nm).join('<NM>/');
  if (s.indexOf(rootpfx) >= 0) s = s.split(rootpfx).join('<ROOT>/');
  if (s === CFG.ROOT) s = '<ROOT>';
  if (home && s.indexOf(home) >= 0) s = s.split(home).join('<HOME>/');
  s = s.replace(TMP_RE, '<TMP>');
  s = s.replace(EPOCHS_RE, function (all, pre, digits) {
    var n = parseInt(digits, 10);
    if (n >= 1e12 && n <= 4e12) { bump('epoch_ms_in_string'); return pre + '<EPOCH_MS>'; }
    return all;
  });
  s = s.replace(ISO_RE, '<ISO>');
  s = s.replace(UUID_RE, '<UUID>');
  s = s.replace(PORT_RE, '$1:<PORT>');
  if (s !== before) bump('string');
  return s;
}

// Only normalize integers that look like epoch milliseconds (2001-09 to 2096-10). A real business
// value in this range is very unlikely, while Date.now() leaking into an argument is the most
// common source of nondeterminism. Hits are counted in _meta.
function normNum(n) {
  if (CFG.NONORM) return null;
  if (typeof n !== 'number' || !isFinite(n)) return null;
  if (Math.floor(n) === n && n >= 1e12 && n <= 4e12) { bump('epoch_ms'); return '<EPOCH_MS>'; }
  return null;
}

function relPath(f) {
  if (typeof f !== 'string' || !f) return '?';
  var r = CFG.ROOT + path.sep;
  if (f.indexOf(r) === 0) return f.slice(r.length);
  return normStr(f);
}

// ------------------------------------------------------------------ serialization
function truncStr(s) {
  if (s.length > CFG.STRMAX) return s.slice(0, CFG.STRMAX) + '…[truncated ' + (s.length - CFG.STRMAX) + ' chars]';
  return s;
}

function tag(v) { return Object.prototype.toString.call(v); }

function isThenable(v) {
  if (!v) return false;
  var t = typeof v;
  if (t !== 'object' && t !== 'function') return false;
  try { return typeof v.then === 'function'; } catch (e) { return false; }
}

function unwrapProxy(v) {
  if (v === null) return v;
  var t = typeof v;
  if (t !== 'object' && t !== 'function') return v;
  try {
    var raw = ST.unwrapMap.get(v);
    return raw === undefined ? v : raw;
  } catch (e) { return v; }
}

/**
 * Length cap for a whole value. Per-string truncation (STRMAX) cannot stop a three-level nested
 * object from producing thousands of characters, and hundreds of such records in one trace would
 * drown the diff report.
 *
 * After truncation the original length and a deterministic hash are appended: keeping only a prefix
 * would make two values that differ only in their tails look identical, which silently erases a real
 * difference and is worse than a large log. With the hash they stay distinguishable at a fixed length.
 */
function fnv1a(str) {
  var h = 0x811c9dc5;
  for (var i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return ('0000000' + h.toString(16)).slice(-8);
}

/**
 * When a container is truncated by ITEMS, compute a fingerprint of the dropped part.
 * Writing only [+N more] would make two values that differ only after the 13th element compare as
 * identical, silently erasing a real difference. The fingerprint covers at most 200 elements; beyond
 * that the record says so.
 */
function tailDigest(getAt, from, total) {
  var lim = Math.min(total, from + 200);
  var acc = '';
  for (var i = from; i < lim; i++) {
    try { acc += i + '\u0001' + repr0(getAt(i), 1, []) + '\u0002'; } catch (e) { acc += i + '\u0001?\u0002'; }
  }
  return '[+' + (total - from) + ' more, h=' + fnv1a(acc) + (lim < total ? ', h covers 200' : '') + ']';
}

function capValue(s) {
  if (typeof s !== 'string' || s.length <= CFG.VALMAX) return s;
  bump('value_capped');
  return s.slice(0, CFG.VALMAX) + '…[value truncated, len=' + s.length + ', h=' + fnv1a(s) + ']';
}

function repr(v, depth, seen) {
  try { return capValue(repr0(v, depth, seen || [])); } catch (e) { return '[reprFailed:' + (e && e.name) + ']'; }
}

function repr0(v, depth, seen) {
  if (v === undefined) return 'undefined';
  if (v === null) return 'null';
  var t = typeof v;
  if (t === 'boolean') return v ? 'true' : 'false';
  if (t === 'number') {
    if (v !== v) return 'NaN';
    if (v === Infinity) return 'Infinity';
    if (v === -Infinity) return '-Infinity';
    if (v === 0 && 1 / v === -Infinity) return '-0';
    var nn = normNum(v);
    if (nn !== null) return nn;
    return String(v);
  }
  if (t === 'bigint') return String(v) + 'n';
  if (t === 'string') return JSON.stringify(truncStr(normStr(v)));
  if (t === 'symbol') return normStr(String(v));
  if (t === 'function') {
    var raw = unwrapProxy(v);
    var nm = '';
    try { nm = raw.name; } catch (e) { nm = ''; }
    return nm ? '[Function: ' + nm + ']' : '[Function (anonymous)]';
  }
  v = unwrapProxy(v);
  if (seen.indexOf(v) >= 0) return '[Circular]';
  var tg = tag(v);
  if (tg === '[object Date]') { bump('date'); return '[Date]'; }
  if (tg === '[object RegExp]') return String(v);
  if (typeof Buffer !== 'undefined' && Buffer.isBuffer && Buffer.isBuffer(v)) {
    return '[Buffer len=' + v.length + ' ' + v.slice(0, 16).toString('hex') + ']';
  }
  if (v instanceof Error) {
    return '[' + (v.name || 'Error') + ': ' + truncStr(normStr(String(v.message))) + ']';
  }
  if (depth <= 0) return '[MaxDepth ' + tg + ']';
  seen.push(v);
  try {
    if (Array.isArray(v)) {
      var n = Math.min(v.length, CFG.ITEMS);
      var parts = [];
      for (var i = 0; i < n; i++) parts.push(repr0(v[i], depth - 1, seen));
      if (v.length > n) parts.push('…' + tailDigest(function (i) { return v[i]; }, n, v.length));
      return '[' + parts.join(', ') + ']';
    }
    if (tg === '[object Map]') {
      var mp = [], c = 0;
      v.forEach(function (val, key) {
        if (c < CFG.ITEMS) mp.push(repr0(key, depth - 1, seen) + ' => ' + repr0(val, depth - 1, seen));
        c++;
      });
      if (c > CFG.ITEMS) mp.push('…[+' + (c - CFG.ITEMS) + ' more]');
      return 'Map{' + mp.join(', ') + '}';
    }
    if (tg === '[object Set]') {
      var sp = [], sc = 0;
      v.forEach(function (val) {
        if (sc < CFG.ITEMS) sp.push(repr0(val, depth - 1, seen));
        sc++;
      });
      if (sc > CFG.ITEMS) sp.push('…[+' + (sc - CFG.ITEMS) + ' more]');
      return 'Set{' + sp.join(', ') + '}';
    }
    if (isThenable(v)) return '[Promise]';
    var ctor = '';
    try {
      var proto = Object.getPrototypeOf(v);
      if (proto === null) ctor = '[NullProto] ';
      else if (proto.constructor && proto.constructor.name && proto.constructor.name !== 'Object') {
        ctor = proto.constructor.name + ' ';
      }
    } catch (e) { ctor = ''; }
    var keys;
    try { keys = Object.keys(v); } catch (e) { keys = []; }
    var kn = Math.min(keys.length, CFG.ITEMS);
    var kp = [];
    for (var j = 0; j < kn; j++) {
      var k = keys[j], sv;
      var d = null;
      try { d = Object.getOwnPropertyDescriptor(v, k); } catch (e) { d = null; }
      if (d && d.get) sv = '[Getter]';
      else { try { sv = repr0(v[k], depth - 1, seen); } catch (e) { sv = '[readFailed]'; } }
      kp.push(JSON.stringify(k) + ': ' + sv);
    }
    if (keys.length > kn) {
      kp.push('…' + tailDigest(function (i) {
        var kk = keys[i], dd = null;
        try { dd = Object.getOwnPropertyDescriptor(v, kk); } catch (e) { dd = null; }
        return (dd && dd.get) ? ('[Getter ' + kk + ']') : [kk, v[kk]];
      }, kn, keys.length));
    }
    return ctor + '{' + kp.join(', ') + '}';
  } finally {
    seen.pop();
  }
}

// ------------------------------------------------------------------ call site
/**
 * Normalize the file name of a stack frame into a filesystem path.
 *
 * ESM modules appear in stack frames as URLs such as `file:///w/src/index.js:185:26`, while all the
 * checks below (is it an upstream-internal frame, is it under node_modules, how to compute the
 * relative path) are written for filesystem paths. Without this, the "path must start with /" filter
 * in callsite would skip ESM frames entirely, the at field would degrade to ?, and the rule
 * "upstream-internal re-entrant calls are not recorded" would stop working.
 * CommonJS frames do not start with file:// and never reach this branch, so their behavior is unchanged.
 */
function frameFile(f) {
  if (typeof f !== 'string' || f.indexOf('file://') !== 0) return f;
  try {
    return require('url').fileURLToPath(f.split('?')[0]);
  } catch (e) { return f; }
}

function inTargetDir(f) {
  if (typeof f !== 'string' || !f) return false;
  for (var p in ST.targetDirs) {
    if (f.indexOf(ST.targetDirs[p]) === 0) return true;
  }
  return false;
}

function isUnderNM(f) {
  if (typeof f !== 'string' || !f) return false;
  return f.indexOf(path.join(CFG.ROOT, 'node_modules') + path.sep) === 0
    || f.indexOf(path.sep + 'node_modules' + path.sep) >= 0;
}

var FRAME_RE = /\(?([^()\s]+):(\d+):(\d+)\)?$/;

/**
 * Return the first stack frame that belongs neither to this file nor to node builtins, as "lib/Foo.js:25".
 * If that frame lies inside the upstream package directory, return null: that is a re-entrant call
 * inside the library, which is not recorded.
 */
function callsite() {
  var lim = Error.stackTraceLimit;
  var o = {};
  try {
    Error.stackTraceLimit = 16;
    Error.captureStackTrace(o, callsite);
  } catch (e) { return '?'; } finally { Error.stackTraceLimit = lim; }
  var st = o.stack;
  if (typeof st !== 'string') return '?';
  var lines = st.split('\n');
  for (var i = 1; i < lines.length; i++) {
    var m = FRAME_RE.exec(lines[i].trim());
    if (!m) continue;
    var f = frameFile(m[1]);
    if (f === __filename) continue;
    if (f.indexOf('internal/') === 0 || f.indexOf('node:') === 0 || f.indexOf('/') !== 0) continue;
    if (inTargetDir(f)) return null;
    return relPath(f) + ':' + m[2];
  }
  return '?';
}

/** Same as callsite, but a frame inside the upstream package is not rejected: a callback is exactly the library calling back into the downstream project. */
function callsiteAny() {
  var lim = Error.stackTraceLimit;
  var o = {};
  try {
    Error.stackTraceLimit = 16;
    Error.captureStackTrace(o, callsiteAny);
  } catch (e) { return '?'; } finally { Error.stackTraceLimit = lim; }
  var st = o.stack;
  if (typeof st !== 'string') return '?';
  var lines = st.split('\n');
  for (var i = 1; i < lines.length; i++) {
    var m = FRAME_RE.exec(lines[i].trim());
    if (!m) continue;
    var f = frameFile(m[1]);
    if (f === __filename) continue;
    if (f.indexOf('internal/') === 0 || f.indexOf('node:') === 0 || f.indexOf('/') !== 0) continue;
    return relPath(f) + ':' + m[2];
  }
  return '?';
}

// ------------------------------------------------------------------ recording
/**
 * Reference-sharing signature between the return value and the arguments.
 *
 * _.clone(args, true) is a deep clone in lodash 3 and degrades to a shallow clone in lodash 4; both
 * return field-by-field identical content, and the only difference is whether the returned container
 * still holds the original objects. Comparing literal values alone can never reveal this kind of
 * break, so "which object is identical to which" must be recorded as well. Only the first few
 * arguments, only own enumerable keys, getters skipped: the cost is bounded and no side effect is triggered.
 */
function aliasSig(args, out) {
  if (out === null || (typeof out !== 'object' && typeof out !== 'function')) return '';
  var raw = unwrapProxy(out);
  var parts = [];
  var maxA = Math.min(args.length, 4);
  for (var i = 0; i < maxA; i++) {
    var a = unwrapProxy(args[i]);
    if (a === null || (typeof a !== 'object' && typeof a !== 'function')) continue;
    if (raw === a) { parts.push('ret==arg' + i); continue; }
    var ka;
    try { ka = Object.keys(a); } catch (e) { continue; }
    var n = Math.min(ka.length, CFG.ITEMS);
    var shared = [];
    for (var k = 0; k < n; k++) {
      var key = ka[k], da = null, db = null;
      try {
        da = Object.getOwnPropertyDescriptor(a, key);
        db = Object.getOwnPropertyDescriptor(raw, key);
      } catch (e) { continue; }
      if (!da || !db || da.get || db.get) continue;
      var v = da.value;
      if (v !== null && (typeof v === 'object' || typeof v === 'function') && v === db.value) shared.push(key);
    }
    if (shared.length) parts.push('ret[' + shared.join(',') + ']===arg' + i + '[same]');
  }
  return parts.join(' ');
}

function beginRecord(fnPath, args, anyCaller) {
  if (ST.truncated) return null;
  var site = anyCaller ? callsiteAny() : callsite();
  if (site === null) return null;
  var rec = { i: ST.seq++, fn: fnPath, caller: site, argc: args.length, args: {} };
  var n = Math.min(args.length, CFG.ITEMS);
  for (var k = 0; k < n; k++) rec.args[String(k)] = repr(args[k], CFG.DEPTH, []);
  if (args.length > n) rec.args['rest'] = '[+' + (args.length - n) + ' more]';
  return rec;
}

function pushRecord(rec) {
  ST.records.push(rec);
  if (ST.records.length >= CFG.MAX) ST.truncated = true;
}

function finishRecord(rec, out, args) {
  rec.ret = repr(out, CFG.DEPTH, []);
  try {
    var al = aliasSig(args || [], out);
    if (al) rec.alias = al;
  } catch (e) { }
  if (CFG.PROMISE && isThenable(out)) {
    rec._p = 'pending';
    try {
      out.then(
        function (val) { rec._p = 'fulfilled'; rec._pv = repr(val, CFG.DEPTH, []); },
        function (err) { rec._p = 'rejected'; rec._pv = repr(err, CFG.DEPTH, []); }
      );
    } catch (e) { rec._p = 'observe_failed'; }
  }
  pushRecord(rec);
}

// ------------------------------------------------------------------ wrapping
function wrappable(v) {
  var t = typeof v;
  if (t === 'function') return true;
  if (t !== 'object' || v === null) return false;
  if (Array.isArray(v)) return false;
  var tg = tag(v);
  if (tg === '[object Date]' || tg === '[object RegExp]' || tg === '[object Map]' || tg === '[object Set]') return false;
  if (typeof Buffer !== 'undefined' && Buffer.isBuffer && Buffer.isBuffer(v)) return false;
  if (v instanceof Error) return false;
  return true;
}

function cacheGet(target, key) {
  var slot;
  try { slot = ST.wrapCache.get(target); } catch (e) { return undefined; }
  return slot ? slot[key] : undefined;
}

function cacheSet(target, key, proxy) {
  try {
    var slot = ST.wrapCache.get(target);
    if (!slot) { slot = Object.create(null); ST.wrapCache.set(target, slot); }
    slot[key] = proxy;
    ST.unwrapMap.set(proxy, target);
  } catch (e) { /* the recorder must never affect the program under test */ }
}

function wrap(target, fnPath, memberDepth, retDepth) {
  if (!wrappable(target)) return target;
  if (memberDepth > CFG.WRAPDEPTH) return target;
  var key = fnPath + '#' + memberDepth + '#' + retDepth;
  var hit = cacheGet(target, key);
  if (hit !== undefined) return hit;
  var handler = {
    get: function (t, prop) {
      var val;
      try { val = t[prop]; } catch (e) { return undefined; }
      if (typeof prop === 'symbol') return val;
      if (prop === 'prototype' || prop === 'constructor' || prop === '__proto__') return val;
      var d = null;
      try { d = Object.getOwnPropertyDescriptor(t, prop); } catch (e) { d = null; }
      // Proxy invariant: a non-configurable, non-writable data property must be returned as is, otherwise a TypeError is thrown
      if (d && d.configurable === false && ('value' in d) && d.writable === false) return val;
      if (d && d.get) return val;
      if (!wrappable(val)) return val;
      return wrap(val, fnPath + '.' + String(prop), memberDepth + 1, retDepth);
    },
    apply: function (t, thisArg, args) {
      return onCall(t, thisArg, args, fnPath, retDepth);
    },
    construct: function (t, args) {
      return onConstruct(t, args, fnPath, retDepth);
    }
  };
  var p;
  try { p = new Proxy(target, handler); } catch (e) { return target; }
  cacheSet(target, key, p);
  return p;
}

function maybeWrapReturn(out, fnPath, retDepth) {
  // Only returned functions are wrapped again. A returned object is often the very object the caller
  // passed in (_.merge returns dest), and replacing it with a proxy would break the downstream === checks.
  if (CFG.RETWRAP <= 0) return out;
  if (retDepth >= CFG.RETWRAP) return out;
  if (typeof out !== 'function') return out;
  return wrap(out, fnPath + '()', 0, retDepth + 1);
}

/**
 * Wrap the function arguments passed to the library, recording the arguments and return value when
 * the library calls them back.
 * Off by default (ALGO_TRACE_CBARGS=1 enables it): a break of this kind, a changed calling convention
 * of a callback, is only visible here (whilst in async 3 passes a callback to test, async 2 does not),
 * but wrapping callbacks perturbs the program under test more easily than wrapping exports (many
 * libraries inspect a callback's length, toString or Symbol markers), so it is enabled per case, and
 * the exit code must be checked for changes every time.
 */
function wrapCallbackArg(fn, fnPath, idx) {
  if (typeof fn !== 'function') return fn;
  var key = 'cb#' + fnPath + '#' + idx;
  var hit = cacheGet(fn, key);
  if (hit !== undefined) return hit;
  var handler = {
    apply: function (t, thisArg, args) {
      var rec = null;
      try {
        if (!ST.truncated) {
          rec = { i: ST.seq++, fn: fnPath + '#arg' + idx, caller: callsiteAny(), argc: args.length, args: {} };
          var n = Math.min(args.length, CFG.ITEMS);
          for (var k = 0; k < n; k++) rec.args[String(k)] = repr(args[k], CFG.DEPTH, []);
          if (args.length > n) rec.args['rest'] = '[+' + (args.length - n) + ' more]';
        }
      } catch (e) { rec = null; }
      // CBARGS>=2: also wrap the objects the library passes into this callback. That is how express's
      // res arrives; the break is the downstream call res.send(400, msg) on it, and res is never the
      // return value of a boundary call, so without this path it is invisible. The perturbation risk
      // is clearly higher, hence a separate level; check the exit code before using it.
      var cbArgs = args;
      if (CFG.CBARGS >= 2) {
        try {
          cbArgs = [];
          for (var j = 0; j < args.length; j++) {
            var v = args[j];
            cbArgs.push(wrappable(v) ? wrap(v, fnPath + '#arg' + idx + ':p' + j, 0, 1) : v);
          }
        } catch (e) { cbArgs = args; }
      }
      var out;
      try {
        out = Reflect.apply(t, unwrapProxy(thisArg), cbArgs);
      } catch (err) {
        if (rec) { try { rec.ret = 'throw ' + repr(err, CFG.DEPTH, []); pushRecord(rec); } catch (e) { } }
        throw err;
      }
      if (rec) { try { rec.ret = repr(out, CFG.DEPTH, []); pushRecord(rec); } catch (e) { } }
      return out;
    }
  };
  var p;
  try { p = new Proxy(fn, handler); } catch (e) { return fn; }
  cacheSet(fn, key, p);
  return p;
}

function onCall(target, thisArg, args, fnPath, retDepth) {
  var rec = null;
  // retDepth > 0 means this callable was handed back to the downstream project by the library (e.g. the
  // product of promisify). Who triggers it directly on the stack is secondary: the hop exists because
  // the downstream project holds it, so it is not dropped as an upstream-internal re-entry.
  try { rec = beginRecord(fnPath, args, retDepth > 0); } catch (e) { rec = null; }
  var callArgs = args;
  if (CFG.CBARGS && rec) {
    try {
      callArgs = [];
      for (var ci = 0; ci < args.length; ci++) {
        callArgs.push(typeof args[ci] === 'function' ? wrapCallbackArg(args[ci], fnPath, ci) : args[ci]);
      }
    } catch (e) { callArgs = args; }
  }
  var out;
  try {
    out = Reflect.apply(target, unwrapProxy(thisArg), callArgs);
  } catch (err) {
    if (rec) { try { rec.ret = 'throw ' + repr(err, CFG.DEPTH, []); pushRecord(rec); } catch (e) { } }
    throw err;
  }
  if (rec) { try { finishRecord(rec, out, args); } catch (e) { } }
  try { return maybeWrapReturn(out, fnPath, retDepth); } catch (e) { return out; }
}

function onConstruct(target, args, fnPath, retDepth) {
  var rec = null;
  try { rec = beginRecord('new ' + fnPath, args); } catch (e) { rec = null; }
  var out;
  try {
    out = Reflect.construct(target, args);
  } catch (err) {
    if (rec) { try { rec.ret = 'throw ' + repr(err, CFG.DEPTH, []); pushRecord(rec); } catch (e) { } }
    throw err;
  }
  if (rec) {
    try {
      rec.ret = repr(out, CFG.DEPTH, []);
      var al = aliasSig(args, out);
      if (al) rec.alias = al;
      pushRecord(rec);
    } catch (e) { }
  }
  return out;
}

// ------------------------------------------------------------------ integrity check
// A rewritten upstream namespace (monkey patch / wrapper) is hard evidence of symptom masking. At the
// first hook, a reference to each own property of the library's export object is recorded; at process
// exit each one is checked to still be the same value.
function snapshotExports(pkg, exp) {
  if (ST.snapshots[pkg]) return;
  var snap = { keys: [], vals: Object.create(null) };
  try {
    var keys = Object.keys(exp);
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      var d = Object.getOwnPropertyDescriptor(exp, k);
      if (d && d.get) continue;
      snap.keys.push(k);
      snap.vals[k] = exp[k];
    }
    snap.ref = exp;
  } catch (e) { }
  ST.snapshots[pkg] = snap;
}

/**
 * Hook entry of the ESM side, called once at module evaluation by the shim generated by js_esm_trace_loader.mjs.
 *
 * Recording, normalization and output all reuse the CommonJS side; this function only does two things:
 * mark this package as actually hooked, and wrap the exports with the same path naming as the CommonJS side.
 *
 * Consistent path naming is what makes the records of the two sides comparable. Under CommonJS,
 * `import chokidar from 'chokidar'` yields the whole module.exports and is recorded as `chokidar.watch`;
 * so the default export must use the package name itself as its path. Writing `chokidar.default.watch`
 * would give the same call two different names in the intact and broken states, and the comparison
 * would degrade into a pile of spurious "reached on one side only" entries.
 *
 * Returns a plain object; the namespace itself (a non-writable exotic object) is not modified.
 */
function esmAttach(pkg, ns, names) {
  var out = Object.create(null);
  if (!ST || !ST.installed) {
    // Recorder not installed: pass through unchanged; never affect the program under test.
    try {
      if (ns && 'default' in ns) out.default = ns.default;
      for (var k = 0; k < names.length; k++) out[names[k]] = ns[names[k]];
    } catch (e) { }
    return out;
  }
  try {
    ST.esmSeen = true;
    ST.reqStats.total++;
    if (CFG.MODE !== 'active') {
      if (ns && 'default' in ns) out.default = ns.default;
      for (var q = 0; q < names.length; q++) out[names[q]] = ns[names[q]];
      return out;
    }
    ST.reqStats.hooked++;
    ST.hooked[pkg] = true;
    snapshotExports(pkg, ns);
    if (ns && 'default' in ns) out.default = wrap(ns.default, pkg, 0, 0);
    for (var i = 0; i < names.length; i++) {
      var n = names[i];
      if (n === 'default') continue;
      out[n] = wrap(ns[n], pkg + '.' + n, 1, 0);
    }
  } catch (e) {
    try {
      if (ns && 'default' in ns && out.default === undefined) out.default = ns.default;
      for (var j = 0; j < names.length; j++) {
        if (out[names[j]] === undefined) out[names[j]] = ns[names[j]];
      }
    } catch (e2) { }
  }
  return out;
}

function integrityCheck() {
  var hits = [];
  for (var pkg in ST.snapshots) {
    var snap = ST.snapshots[pkg];
    if (!snap || !snap.ref) continue;
    var exp = snap.ref;
    var now;
    try { now = Object.keys(exp); } catch (e) { continue; }
    for (var i = 0; i < snap.keys.length && hits.length < 20; i++) {
      var k = snap.keys[i];
      var d = null;
      try { d = Object.getOwnPropertyDescriptor(exp, k); } catch (e) { d = null; }
      if (!d) { hits.push({ module: pkg, attr: k, how: 'removed' }); continue; }
      if (d.get) { hits.push({ module: pkg, attr: k, how: 'turned_into_getter' }); continue; }
      if (d.value !== snap.vals[k]) {
        hits.push({ module: pkg, attr: k, how: 'replaced', now: repr(d.value, 1, []) });
      }
    }
    for (var j = 0; j < now.length && hits.length < 20; j++) {
      if (snap.keys.indexOf(now[j]) < 0) hits.push({ module: pkg, attr: now[j], how: 'added' });
    }
  }
  return hits;
}

// ------------------------------------------------------------------ output
// fs.mkdirSync on node 8 has no recursive option; create the directories recursively by hand.
function mkdirp(d) {
  if (!d) return;
  try { if (fs.statSync(d).isDirectory()) return; } catch (e) { }
  mkdirp(path.dirname(d));
  try { fs.mkdirSync(d); } catch (e) { }
}

function pad4(n) { return ('0000' + n).slice(-4); }

function dump() {
  if (!ST || ST.dumped) return;
  ST.dumped = true;
  try {
    var integrity = [];
    try { integrity = integrityCheck(); } catch (e) { integrity = []; }
    var lines = [];
    lines.push(JSON.stringify({
      _proc: true,
      ordinal: ST.ordinal,
      pid: process.pid,
      node: process.version,
      script: relPath(process.argv[1] || ''),
      cwd: relPath(process.cwd()),
      scope: CFG.SCOPE,
      mode: CFG.MODE,
      promise_observed: CFG.PROMISE ? 1 : 0,
      callback_args_observed: CFG.CBARGS ? 1 : 0,
      nonorm: CFG.NONORM ? 1 : 0,
      pkgs: CFG.PKGS,
      target_dirs_found: Object.keys(ST.targetDirs).sort(),
      missing_pkgs: ST.missing.slice().sort(),
      hooked_pkgs: Object.keys(ST.hooked).sort(),
      requires: ST.reqStats,
      records: ST.records.length,
      truncated: ST.truncated,
      esm_seen: ST.esmSeen,
      normalizations: ST.norm,
      integrity: integrity
    }));
    for (var i = 0; i < ST.records.length; i++) {
      var r = ST.records[i];
      if (r._p) {
        r.ret = '[Promise ' + r._p + (r._pv !== undefined ? ': ' + r._pv : '') + ']';
        delete r._p; delete r._pv;
      }
      lines.push(JSON.stringify(r));
    }
    fs.writeFileSync(path.join(CFG.DIR, 'proc-' + pad4(ST.ordinal) + '-' + process.pid + '.jsonl'),
      lines.join('\n') + '\n');
  } catch (e) {
    try {
      fs.writeFileSync(path.join(CFG.DIR, 'dumperror-' + process.pid + '.txt'), String(e && e.stack || e));
    } catch (e2) { }
  }
}

// ------------------------------------------------------------------ install
function install() {
  ST = freshState();
  if (!CFG.PKGS.length || !CFG.DIR) return false;
  mkdirp(CFG.DIR);
  // Process ordinal: the merge step sorts by it, so merging several processes (npm wrapper + test process) is deterministic.
  try {
    var cf = path.join(CFG.DIR, '.ord');
    fs.appendFileSync(cf, 'x');
    ST.ordinal = fs.statSync(cf).size;
  } catch (e) { ST.ordinal = 0; }
  // Installed marker: even if the process is killed hard before dumping, it distinguishes "no behavioral difference" from "the recorder never attached".
  try {
    fs.writeFileSync(path.join(CFG.DIR, 'installed-' + pad4(ST.ordinal) + '-' + process.pid + '.txt'),
      'node=' + process.version + '\nargv1=' + relPath(process.argv[1] || '') + '\npkgs=' + CFG.PKGS.join(',') + '\n');
  } catch (e) { }

  for (var i = 0; i < CFG.PKGS.length; i++) {
    var p = CFG.PKGS[i];
    var d = path.join(CFG.ROOT, 'node_modules', p);
    var real = d;
    try { real = fs.realpathSync(d); } catch (e) { real = d; }
    var ok = false;
    try { ok = fs.statSync(real).isDirectory(); } catch (e) { ok = false; }
    if (ok) ST.targetDirs[p] = real + path.sep; else ST.missing.push(p);
  }

  ST.origLoad = Module._load;
  Module._load = function (request, parent, isMain) {
    var exp = ST.origLoad.apply(this, arguments);
    try {
      var resolved = null;
      try { resolved = Module._resolveFilename(request, parent, isMain); } catch (e) { resolved = null; }
      if (typeof resolved === 'string' && /\.mjs$/.test(resolved)) ST.esmSeen = true;
      if (!resolved) return exp;
      var pkg = null;
      for (var q in ST.targetDirs) { if (resolved.indexOf(ST.targetDirs[q]) === 0) { pkg = q; break; } }
      if (!pkg) return exp;
      ST.reqStats.total++;
      var pf = (parent && parent.filename) ? parent.filename : '';
      if (pf && inTargetDir(pf)) { ST.reqStats.internal++; return exp; }
      if (CFG.MODE !== 'active') { return exp; }
      if (CFG.SCOPE === 'project' && pf && isUnderNM(pf)) { ST.reqStats.intermediate++; return exp; }
      ST.reqStats.hooked++;
      ST.hooked[pkg] = true;
      snapshotExports(pkg, exp);
      return wrap(exp, pkg, 0, 0);
    } catch (e) { return exp; }
  };

  try {
    if (process.env.npm_package_type === 'module') ST.esmSeen = true;
    var pj = path.join(CFG.ROOT, 'package.json');
    if (fs.existsSync(pj)) {
      var j = JSON.parse(fs.readFileSync(pj, 'utf8'));
      if (j && j.type === 'module') ST.esmSeen = true;
    }
  } catch (e) { }

  process.on('exit', dump);
  ST.installed = true;
  return true;
}

var autoInstalled = install();

module.exports = {
  installed: autoInstalled,
  install: install,
  dump: dump,
  CFG: CFG,
  __internals: {
    normStr: normStr,
    normNum: normNum,
    repr: repr,
    relPath: relPath,
    truncStr: truncStr,
    callsite: callsite,
    wrap: wrap,
    esmAttach: esmAttach,
    frameFile: frameFile,
    wrappable: wrappable,
    isThenable: isThenable,
    fnv1a: fnv1a,
    capValue: capValue,
    integrityCheck: integrityCheck,
    inTargetDir: inTargetDir,
    isUnderNM: isUnderNM,
    state: function () { return ST; }
  }
};
