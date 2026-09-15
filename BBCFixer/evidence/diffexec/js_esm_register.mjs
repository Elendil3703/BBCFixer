/**
 * Register the ESM loader hook into this process. Preloaded via --import in NODE_OPTIONS.
 *
 * --experimental-loader is not used because it prints an ExperimentalWarning to stderr; that warning
 * would leak into test-output.txt, polluting the parsing of failed tests and possibly triggering
 * downstream assertions on stderr. module.register is available since Node 18.19 and prints no such warning.
 */
import { register } from 'node:module';
register('./js_esm_trace_loader.mjs', import.meta.url);
