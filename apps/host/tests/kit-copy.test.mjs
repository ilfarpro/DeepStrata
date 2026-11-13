import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve} from 'node:path';
const root=resolve(import.meta.dirname,'..');
const repo=resolve(root,'..','..');
/* The app owns the only kit components.css in this fork: the standalone server page (serve/web) is not part of it, so
   nothing has to be kept in step. The guard stays so that an accidental copy cannot come back unnoticed. */
test('the app keeps the only components.css (no serve/web copy to sync)',()=>{
 const app=resolve(root,'src/styles/kit/components.css');
 assert.ok(existsSync(app),'src/styles/kit/components.css is missing');
 assert.ok(!existsSync(resolve(repo,'serve/web/components.css')),'serve/web is not in this fork: remove that copy, the app owns the file');
});
/* A package.json script that runs a file under scripts/ must point at a file that exists (the removed sync:kit pointed at a design folder that is not in the repository). */
test('every npm script that runs a scripts/ file points at a file that exists',()=>{
 const scripts=JSON.parse(readFileSync(resolve(root,'package.json'),'utf8')).scripts;
 for(const [name,command] of Object.entries(scripts)){
  for(const m of command.matchAll(/\bnode\s+(scripts\/[\w./-]+)/g))assert.ok(existsSync(resolve(root,m[1])),`npm run ${name} runs ${m[1]}, which does not exist`);
 }
});
