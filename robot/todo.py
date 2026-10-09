"""Les vidéos finies, à poster à la main sur TikTok et YouTube (Lou, 9 oct. 2026).

Dans chaque dossier de compte de OneDrive « AXIOM acquisition » (ChadSciences, Octave, Théo Rème, Singe explique DA,
Spouf…) :
  « 00 - Vidéos finales à poster »  une vidéo y entre quand Lou dit qu'elle est finie (Claude l'y range), OU dès que le
                                    robot la voit sortir sur un compte TikTok, Instagram ou YouTube (« si elle sort,
                                    c'est qu'elle est postable ») — il l'y range alors tout seul ;
  « 01 - Déjà postées »             le robot l'y range quand elle est sortie sur TikTok ET sur YouTube.
Une vidéo = « NN - Titre.mp4 », sa légende à coller = « NN - Titre - légende.txt » (ou « NN - Titre.txt »).
Reconnaître une sortie : TikTok et Instagram = empreinte image + son de la vidéo en ligne (téléchargée) ; YouTube refuse
le téléchargement depuis GitHub → durée (page de la vidéo) + miniature comparée aux images du fichier (calibré le
9 oct. : 0.96–1.00 pour la bonne vidéo, ≤ 0.72 sinon), ou à défaut un titre quasi identique.
JARVIS les affiche en tête du Rapport, avec où chacune est déjà sortie. Réglages : state/todo_accounts.json.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests

import fingerprint as fpr
import onedrive as od
import tiktok

ACQ = "AXIOM acquisition"
TODO = "00 - Vidéos finales à poster"
DONE = "01 - Déjà postées"
SKIP = {"AXIOM Insta quiz", DONE}      # + tout dossier qui commence par « _ » (archives, robot)
TARGETS = ("tiktok", "youtube")       # où Lou poste à la main ; Instagram se fait tout seul (robot)
DUR_TOL = 2.0
MIN_IMAGE, MIN_SON = 0.75, 0.85
MIN_THUMB = 0.88                      # miniature YouTube ↔ images du fichier
LOOKBACK = 30 * 86400                 # sorties examinées : celles des 30 derniers jours
RECHECK = 3 * 86400                   # une sortie sans fichier est revue 3 jours (le fichier arrive parfois après)
STATE = Path(__file__).resolve().parents[1] / "state"


def cfg_for(cfg: dict, top: str) -> dict:
    """Réglages du dossier de compte : par son nom exact, sinon par mot-clé (les dossiers sont parfois renommés)."""
    if top in cfg:
        return cfg[top]
    n = top.lower()
    for c in cfg.values():
        if any(k in n for k in c.get("match", [])) and not any(k in n for k in c.get("not", [])):
            return c
    return {}


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) if iso else 0


def norm(t: str) -> str:
    t = re.sub(r"[#@]\S+", " ", (t or "").lower())
    return re.sub(r"[^\w]+", " ", t).strip()


def sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def folder_child(parent_id: str, name: str, create: bool = False) -> str | None:
    for c in od.children(parent_id):
        if "folder" in c and c["name"] == name:
            return c["id"]
    if not create:
        return None
    r = od.call("POST", f"/me/drive/items/{parent_id}/children",
                json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
    r.raise_for_status()
    return r.json()["id"]


def scan() -> tuple[list[dict], dict]:
    """Toutes les vidéos de « AXIOM acquisition » (hors archives, quiz et déjà postées), avec leur légende éventuelle.
    Renvoie aussi {dossier de compte: id}."""
    root = od.by_path(ACQ)
    tops = {}
    out = []
    stack = [(root["id"], "", None)]
    while stack:
        fid, rel, top = stack.pop()
        kids = od.children(fid)
        txt = {k["name"]: k for k in kids if k["name"].lower().endswith(".txt")}
        for k in kids:
            name = k["name"]
            if "folder" in k:
                if name.startswith("_") or name in SKIP:
                    continue
                if top is None:
                    tops[name] = k["id"]
                stack.append((k["id"], f"{rel}{name}/", top if top is not None else name))
            elif name.lower().endswith((".mp4", ".mov")):
                stem = name.rsplit(".", 1)[0]
                cap = txt.get(f"{stem} - légende.txt") or txt.get(f"{stem}.txt")
                out.append({"id": k["id"], "name": name, "rel": rel + name, "top": top or "", "parent": fid,
                            "in_todo": rel.rstrip("/").endswith(TODO),
                            "title": stem.split(" - ", 1)[-1] if re.match(r"^\d+\s*-", stem) else stem,
                            "dur": ((k.get("video") or {}).get("duration") or 0) / 1000, "added": _ts(k.get("createdDateTime")),
                            "web": k.get("webUrl"), "ctag": k.get("cTag"),
                            "caption_id": cap["id"] if cap else None, "caption_name": cap["name"] if cap else None})
    return out, tops


# ------------------------------------------------------------------ sorties en ligne → fichiers

def match_posts(db: dict, files: list[dict], work: Path, ig_media: dict, log):
    """Pour chaque vidéo sortie récemment (TikTok, Instagram, YouTube), le fichier OneDrive dont elle vient."""
    pm = db.setdefault("posts_map", {})        # "tt:id" → {"f": item_id | "", "t": vérifié le}
    fps: dict[str, dict] = {}

    def fp_file(f):
        if f["id"] not in fps:
            fps[f["id"]] = fpr.compute(od.download(f["id"], work / "todo" / f"{f['id']}.mp4"))
        return fps[f["id"]]

    now = time.time()
    for key, v in sorted(db["videos"].items(), key=lambda kv: -kv[1]["at"]):
        if v["p"] not in ("tiktok", "instagram", "youtube") or now - v["at"] > LOOKBACK:
            continue
        if v.get("r"):  # posté par le robot (quiz, ou repost dont on connaît déjà le fichier)
            continue
        if v["p"] == "youtube" and not v.get("dur"):  # sans durée, il faudrait comparer à tous les fichiers
            continue
        old = pm.get(key)
        if old and (old["f"] or now - v["at"] > RECHECK or now - old["t"] < 20 * 3600):
            continue
        cands = [f for f in files if f["dur"] and f["added"] <= v["at"] + 86400
                 and (not v.get("dur") or abs(f["dur"] - v["dur"]) <= DUR_TOL)]
        found = ""
        try:
            if v["p"] in ("tiktok", "instagram") and cands:
                if v["p"] == "tiktok":
                    path = tiktok.download(v["url"], work / "tt" / v["id"], best=False)
                elif ig_media.get(v["id"]):
                    path = work / "ig" / f"{v['id']}.mp4"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(requests.get(ig_media[v["id"]], timeout=120).content)
                else:
                    path = None
                if path:
                    a = fpr.compute(path)
                    for f in cands:
                        if abs(f["dur"] - a["duration"]) > DUR_TOL:
                            continue
                        s = fpr.compare(a, fp_file(f))
                        if s["image"] >= MIN_IMAGE and s["son"] >= MIN_SON:
                            found = f["id"]
                            break
            elif v["p"] == "youtube" and cands:
                img = work / "yt" / f"{v['id']}.jpg"
                img.parent.mkdir(parents=True, exist_ok=True)
                img.write_bytes(requests.get(v.get("thumb_src") or f"https://i.ytimg.com/vi/{v['id']}/oardefault.jpg",
                                             timeout=60).content)
                vec = fpr.image_vec(img)
                best, score = None, 0.0
                for f in cands:
                    sc = fpr.best_frame(fp_file(f)["frames"], vec)
                    if sc > score:
                        best, score = f, sc
                titles = lambda f: [f["title"], (db.get("todo_caps", {}).get(f["id"]) or {}).get("text", "")] + [  # noqa: E731
                    db["videos"][k].get("t", "") for k, m in pm.items() if m.get("f") == f["id"] and k in db["videos"]]
                if best and score >= MIN_THUMB:
                    found = best["id"]
                elif best and score >= 0.80 and max(sim(v.get("t", ""), t) for t in titles(best)) >= 0.6:
                    found = best["id"]
                else:  # miniature personnalisée : un titre quasi identique suffit
                    for f in cands:
                        if max(sim(v.get("t", ""), t) for t in titles(f)) >= 0.85:
                            found = f["id"]
                            break
        except Exception as ex:  # noqa: BLE001
            log(f"sortie {key} : comparaison impossible ({ex})")
            continue
        pm[key] = {"f": found, "t": now}
        if found:
            f = next(x for x in files if x["id"] == found)
            log(f"sortie {v['p']} @{v['a']} {v['id']} = {f['rel']}")
    for k in [k for k in pm if k not in db["videos"]]:
        del pm[k]


def posts_of(db: dict) -> dict:
    """{item_id: {plateforme: [{a, at, url}]}} : où chaque fichier est déjà sorti."""
    out: dict = {}

    def add(item, p, v):
        out.setdefault(item, {}).setdefault(p, []).append({"a": v.get("a") or v.get("account"), "at": v.get("at") or v.get("posted"),
                                                           "url": v.get("url"), "id": v.get("id")})
    for key, m in db.get("posts_map", {}).items():
        if m.get("f") and key in db["videos"]:
            v = db["videos"][key]
            add(m["f"], v["p"], v)
    rp = STATE / "reposts.json"   # le robot Insta connaît déjà le fichier de chaque vidéo TikTok qu'il reposte
    if rp.exists():
        reposts = json.loads(rp.read_text(encoding="utf-8")).get("videos", {})
        ig = {r["key"]: r for r in (json.loads((STATE / "published_ig.json").read_text(encoding="utf-8"))
                                    if (STATE / "published_ig.json").exists() else [])}
        for r in reposts.values():
            if r.get("source") == "onedrive" and r.get("item_id"):
                add(r["item_id"], "tiktok", {"a": r["account"], "at": r["posted"], "url": r["url"]})
                pub = ig.get(f"tt-{r['id']}")
                if pub:
                    add(r["item_id"], "instagram", {"a": "axiomscience.app", "at": _ts(pub["at"].replace(" ", "T") + "+02:00"),
                                                    "url": pub.get("permalink")})
    for d in out.values():
        for p in d:
            d[p] = sorted({(x["a"], x["at"]): x for x in d[p]}.values(), key=lambda x: x["at"] or 0)
    return out


def _move(item_id: str, folder_id: str):
    od.call("PATCH", f"/me/drive/items/{item_id}", json={"parentReference": {"id": folder_id}}).raise_for_status()


def thumb(it: dict, work: Path) -> Path | None:
    """Miniature 9:16 faite par OneDrive (aucun téléchargement de la vidéo)."""
    d = od.get(f"/me/drive/items/{it['id']}/thumbnails")
    url = ((d.get("value") or [{}])[0].get("large") or {}).get("url")
    if not url:
        return None
    src, dst = work / f"todo-{it['id']}.src", work / f"todo-{it['id']}.jpg"
    src.write_bytes(requests.get(url, timeout=60).content)
    subprocess.run([fpr.FFMPEG, "-v", "error", "-y", "-i", str(src), "-vf",
                    "scale=216:384:force_original_aspect_ratio=increase,crop=216:384", "-q:v", "4", "-frames:v", "1",
                    str(dst)], check=True, timeout=60)
    return dst


def cover(db: dict, it: dict) -> str | None:
    """Déjà sortie sur TikTok : sa vraie couverture (OneDrive, lui, prend la 1re image, parfois blanche)."""
    for p in it.get("posts", {}).get("tiktok", []):
        v = db["videos"].get(f"tt:{p.get('id')}")
        if v and v.get("th"):
            return v["th"]
    return None


def run(db: dict, work: Path, full: bool, blob_put, log, ig_media: dict | None = None) -> None:
    """Met à jour db["todo"] (à poster) et db["todo_done"] (sorties partout, rangées dans « 01 - Déjà postées »)."""
    work.mkdir(parents=True, exist_ok=True)
    cfgp = STATE / "todo_accounts.json"
    cfg = json.loads(cfgp.read_text(encoding="utf-8")) if cfgp.exists() else {}
    files, tops = scan()
    if full:
        match_posts(db, files, work, ig_media or {}, log)
    posts = posts_of(db)
    finals = [f for f in files if f["in_todo"] or posts.get(f["id"])]
    hist = db.setdefault("todo_done", [])
    pending = []
    for f in finals:
        where = posts.get(f["id"], {})
        try:
            if full and all(where.get(p) for p in TARGETS) and f["top"]:
                dest = folder_child(tops[f["top"]], DONE, create=True)
                _move(f["id"], dest)
                if f["caption_id"]:
                    _move(f["caption_id"], dest)
                log(f"sortie partout : {f['rel']} → « {DONE} »")
                hist.append({"id": f["id"], "title": f["title"], "folder": f["top"], "moved": int(time.time()),
                             "posts": where})
                continue
            if full and not f["in_todo"] and f["top"]:
                dest = folder_child(tops[f["top"]], TODO, create=True)
                _move(f["id"], dest)
                if f["caption_id"]:
                    _move(f["caption_id"], dest)
                log(f"sortie sur {', '.join(where)} : {f['rel']} → « {TODO} » (reste : "
                    f"{', '.join(p for p in TARGETS if not where.get(p))})")
        except Exception as ex:  # noqa: BLE001
            log(f"à poster {f['rel']} : rangement impossible ({ex})")
        pending.append({**f, "posts": where})
    db["todo_done"] = [d for d in hist if time.time() - d["moved"] < 30 * 86400]
    # miniatures (une fois par fichier et par version) et légendes
    ths, caps = db.setdefault("todo_th", {}), db.setdefault("todo_caps", {})
    up = []
    for it in pending:
        if ths.get(it["id"], {}).get("ctag") != it["ctag"]:
            try:
                f = thumb(it, work)
                if f:
                    up.append((f, f"jarvis/todo/{it['id'].replace('!', '_')}-{hashlib.md5(str(it['ctag']).encode()).hexdigest()[:8]}.jpg",
                               "image/jpeg", 31536000, it))
            except Exception as ex:  # noqa: BLE001
                log(f"à poster {it['name']} : miniature impossible ({ex})")
        if it["caption_id"] and caps.get(it["id"], {}).get("v") != f"{it['caption_name']}{it['ctag']}":
            try:
                caps[it["id"]] = {"v": f"{it['caption_name']}{it['ctag']}", "text": od.read_text(it["caption_id"]).strip()}
            except Exception:  # noqa: BLE001
                pass
    if up:
        urls = blob_put([(f, p, ct, age) for f, p, ct, age, _ in up])
        for f, p, ct, age, it in up:
            if p in urls:
                ths[it["id"]] = {"ctag": it["ctag"], "url": urls[p]}
    keep = {i["id"] for i in pending}
    for d in (ths, caps):
        for k in [k for k in d if k not in keep]:
            del d[k]
    db["todo"] = [{"id": i["id"], "title": i["title"], "name": i["name"], "folder": i["top"],
                   "label": cfg_for(cfg, i["top"]).get("label") or i["top"] or ACQ,
                   "tiktok": cfg_for(cfg, i["top"]).get("tiktok"), "youtube": cfg_for(cfg, i["top"]).get("youtube"),
                   "dur": round(i["dur"], 1), "added": i["added"], "web": i["web"], "th": cover(db, i) or (ths.get(i["id"]) or {}).get("url"),
                   "caption": (caps.get(i["id"]) or {}).get("text"), "posts": i["posts"]} for i in pending]
    log(f"à poster : {len(db['todo'])} vidéo(s) finie(s) en attente")
