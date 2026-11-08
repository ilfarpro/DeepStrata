/**
 * The app's version, from one place: the host's own package.json, beside `server/` in a checkout and in the installed `host/` folder.
 * A launcher may name it exactly in STRATA_APP_VERSION; a host without a package file next to it reports 'dev'.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const VERSION = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$/;

export function appVersionFrom({ env = process.env, here }) {
  const given = String(env.STRATA_APP_VERSION ?? '').trim();
  if (VERSION.test(given)) return given;
  try {
    const pkg = JSON.parse(readFileSync(join(here, '..', 'package.json'), 'utf8'));
    const declared = String(pkg.version ?? '');
    if (VERSION.test(declared)) return declared;
  } catch { /* not a host folder with a package file */ }
  return 'dev';
}

/** The version "Strata" means on screen and in the update check: the app's own, not Strata Control's (see src/lib/app-version.ts). */
export function appVersionToShow(app, control) {
  const real = value => (typeof value === 'string' && value.trim() && value.trim().toLowerCase() !== 'dev' ? value.trim() : null);
  return real(app) ?? real(control) ?? (typeof app === 'string' && app.trim() ? app.trim() : null);
}
