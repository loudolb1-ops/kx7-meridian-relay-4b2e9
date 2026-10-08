// Envoie des fichiers dans le stockage Vercel Blob de JARVIS (« jarvis-rapport », public).
// node robot/blob_put.mjs manifeste.json   — manifeste = [[fichier, chemin, type, cache_s], …]
// Écrit sur la dernière ligne : {chemin: url publique}. Jeton : BLOB_READ_WRITE_TOKEN (ou BLOB_TOKEN).
import { readFileSync } from 'node:fs';
import { put } from '@vercel/blob';

const token = process.env.BLOB_READ_WRITE_TOKEN || process.env.BLOB_TOKEN;
if (!token) { console.error('BLOB_TOKEN manquant'); process.exit(1); }
const items = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out = {};
let i = 0;
async function worker() {
  while (i < items.length) {
    const [file, pathname, contentType, maxAge] = items[i++];
    const res = await put(pathname, readFileSync(file), {
      access: 'public', token, contentType, addRandomSuffix: false, allowOverwrite: true, cacheControlMaxAge: maxAge,
    });
    out[pathname] = res.url;
  }
}
await Promise.all(Array.from({ length: 6 }, worker));
console.log(JSON.stringify(out));
