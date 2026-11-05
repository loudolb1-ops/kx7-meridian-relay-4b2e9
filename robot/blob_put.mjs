// Envoie des fichiers dans le stockage de JARVIS : Upstash Redis (le même que JARVIS utilise déjà pour la to-do et les vues).
// node robot/blob_put.mjs manifeste.json   — manifeste = [[fichier, chemin, type, cache_s], …]
// Écrit sur la dernière ligne : {chemin: url}.
// 5 nov. 2026 : les deux stores Vercel Blob (« jarvis-prive », « jarvis-rapport ») du compte Hobby ont été suspendus pour
// quota dépassé (« limits-exceeded-suspended », 30 jours) → plus de Vercel Blob du tout :
//   - jarvis/report.json → clé « jarvis:report » (privée : servie par JARVIS /api/report, connexion requise) ;
//   - le reste (vignettes, avatars, miniatures « à poster ») → clé « jf:<chemin> » en base64, expire au bout de 40 jours
//     (la base fait 64 Mo), servie par JARVIS /api/f/<chemin> (connexion requise aussi). L'URL rendue est relative
//     (« /api/f/<chemin> ») : elle marche en ligne et sur la page du PC (jarvis.py relaie /api/f/).
// Le nom du fichier est gardé pour ne rien changer aux appels de report.py / todo.py.
import { readFileSync } from 'node:fs';

const KV = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
const TOKEN = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
const TTL = 40 * 86400;
const REPORT = 'jarvis/report.json';
if (!KV || !TOKEN) { console.error('KV_REST_API_URL / KV_REST_API_TOKEN manquants'); process.exit(1); }

async function cmd(args) {
  for (let k = 0; ; k++) {
    const r = await fetch(KV, { method: 'POST', headers: { Authorization: `Bearer ${TOKEN}` }, body: JSON.stringify(args) });
    if (r.ok) return r.json();
    const msg = `Upstash ${r.status} ${(await r.text()).slice(0, 200)}`;
    if (k >= 2 || (r.status >= 400 && r.status < 500 && r.status !== 429)) throw new Error(msg);
    await new Promise(s => setTimeout(s, 1500 * (k + 1)));
  }
}

const items = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out = {};
let i = 0;
async function worker() {
  while (i < items.length) {
    const [file, pathname] = items[i++];
    const buf = readFileSync(file);
    if (pathname === REPORT) {
      await cmd(['SET', 'jarvis:report', buf.toString('utf8')]);
      out[pathname] = 'kv:jarvis:report';
    } else {
      await cmd(['SET', `jf:${pathname}`, buf.toString('base64'), 'EX', String(TTL)]);
      out[pathname] = `/api/f/${pathname}`;
    }
  }
}
await Promise.all(Array.from({ length: 6 }, worker));
console.log(JSON.stringify(out));
