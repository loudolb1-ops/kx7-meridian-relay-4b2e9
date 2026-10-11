// Envoie des fichiers dans les stockages Vercel Blob de JARVIS.
// node robot/blob_put.mjs manifeste.json   — manifeste = [[fichier, chemin, type, cache_s], …]
// Écrit sur la dernière ligne : {chemin: url}.
//   - jarvis/report.json → store PRIVÉ « jarvis-prive » (jeton BLOB_PRIVE_TOKEN) : JARVIS le sert via /api/report,
//     réservé aux personnes connectées (11 oct. 2026 : le rapport était lisible par tous sur le store public) ;
//   - le reste (vignettes, avatars) → store public « jarvis-rapport » (jeton BLOB_READ_WRITE_TOKEN ou BLOB_TOKEN).
// Jamais de repli vers le public pour un chemin privé : sans jeton privé, l'envoi échoue.
import { readFileSync } from 'node:fs';
import { put } from '@vercel/blob';

const PRIVATE = new Set(['jarvis/report.json']);
const token = process.env.BLOB_READ_WRITE_TOKEN || process.env.BLOB_TOKEN;
const privToken = process.env.BLOB_PRIVE_TOKEN;
const items = JSON.parse(readFileSync(process.argv[2], 'utf8'));
if (items.some(([, p]) => PRIVATE.has(p)) && !privToken) { console.error('BLOB_PRIVE_TOKEN manquant'); process.exit(1); }
if (items.some(([, p]) => !PRIVATE.has(p)) && !token) { console.error('BLOB_TOKEN manquant'); process.exit(1); }
const out = {};
let i = 0;
async function worker() {
  while (i < items.length) {
    const [file, pathname, contentType, maxAge] = items[i++];
    const priv = PRIVATE.has(pathname);
    const res = await put(pathname, readFileSync(file), {
      access: priv ? 'private' : 'public', token: priv ? privToken : token, contentType,
      addRandomSuffix: false, allowOverwrite: true, cacheControlMaxAge: maxAge,
    });
    out[pathname] = res.url;
  }
}
await Promise.all(Array.from({ length: 6 }, worker));
console.log(JSON.stringify(out));
