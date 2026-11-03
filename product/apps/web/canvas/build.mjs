/*
 * F04A/T040 canvas build: repeatable esbuild bundle to the committed static
 * assets. The page never needs npm at runtime or test time.
 *
 *   cd product/apps/web/canvas && npm ci && npm run build
 *
 * Writes apps/web/src/assets/canvas.js (IIFE, global OrcharaCanvas) and
 * apps/web/src/assets/canvas.css.
 */

import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(here, '..', 'src', 'assets');

const result = await build({
  entryPoints: [path.join(here, 'src', 'main.js')],
  outfile: path.join(outDir, 'canvas.js'),
  bundle: true,
  format: 'iife',
  globalName: 'OrcharaCanvasBundle',
  target: ['es2020'],
  minify: false,
  sourcemap: false,
  legalComments: 'none',
  define: {
    __VUE_OPTIONS_API__: 'true',
    __VUE_PROD_DEVTOOLS__: 'false',
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: 'false',
  },
  logLevel: 'info',
});

if (result.errors.length > 0) {
  process.exit(1);
}
console.log('built', path.relative(here, path.join(outDir, 'canvas.js')), 'and canvas.css');
