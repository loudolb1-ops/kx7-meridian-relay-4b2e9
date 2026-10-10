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

Dossiers à tri (réglage « split » facultatif — AUCUN dossier ne l'utilise depuis le 9 oct. ~14:10 : Lou a changé d'avis,
tous les spoufs partent sur TikTok @axiomofficiel, sans tri). Tel qu'il a servi (spoufs / clipping) : une vidéo posée dans « 00 » part dans
le sous-dossier de son compte TikTok selon sa durée — moins d'1 min → « Modulomaths (moins d'1 min) » (@axiomofficiel,
ex-@modulomaths), sinon → « ChadSciences (plus d'1 min) ». Une courte est AUSSI copiée dans « ChadSciences (plus
d'1 min)/À rallonger (je peux tourner) » : pas encore postable sur ChadSciences (règle ≥ 1:01), elle attend les phrases
que Lou enregistre ; une fois rallongée (≥ 1 min), elle remonte d'elle-même dans « ChadSciences ». Dans JARVIS : chaque
sous-dossier a son compte (report.todo[].tiktok) ; « À rallonger » à part (report.todo_hold). Les sous-dossiers sont
reconnus par mot-clé (« modulo », « chadsciences », « rallong ») : Lou peut les renommer.
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
# Un dossier peut viser moins de plateformes : réglage à null dans todo_accounts.json (Lou, 9 oct. ~04:15 : les
# spoofs / clipping sortent sur TikTok @chadsciences + Insta, jamais sur YouTube — pas « à poster sur YouTube »).
DUR_TOL = 2.0
MIN_IMAGE, MIN_SON = 0.75, 0.85
# image quasi identique : le son peut avoir été retraité par la plateforme (10 oct. : TikTok « 1+1 = 2 » de @chadsciences,
# image 0.983 / son 0.822, rendu −17.8 LUFS au lieu de −14.4 → rejeté, carte « à poster » alors que posté).
# Calibré le 11 oct. : vidéos sans rapport ≈ image 0.07–0.10 / son 0.06–0.08.
SURE_IMAGE, SURE_SON = 0.95, 0.55
MATCH_V = 2                           # version des règles : une sortie jugée avec d'anciennes règles est revue tout de suite
MIN_THUMB = 0.88                      # miniature YouTube ↔ images du fichier
LOOKBACK = 30 * 86400                 # sorties examinées : celles des 30 derniers jours
RECHECK = 3 * 86400                   # une sortie sans fichier est revue 3 jours (le fichier arrive parfois après)
STATE = Path(__file__).resolve().parents[1] / "state"
# Dossier « En attente - à rallonger quand je tourne » (hors de « 00 », Lou 9 oct. ~23:15) : des vidéos finies mais trop
# courtes pour leur compte, qui attendent la voix de Lou. Lues pour JARVIS (report.todo_hold, waiting=True : onglet
# Process, pastille « à rallonger ») ; jamais rangées dans 00/01 ni postées par le robot Insta.
WAIT = ("attente", "rallong")


def cfg_for(cfg: dict, top: str) -> dict:
    """Réglages du dossier de compte : par son nom exact, sinon par mot-clé (les dossiers sont parfois renommés)."""
    if top in cfg:
        return cfg[top]
    n = top.lower()
    for c in cfg.values():
        if any(k in n for k in c.get("match", [])) and not any(k in n for k in c.get("not", [])):
            return c
    return {}


def sub_of(c: dict, segs: list[str]) -> str | None:
    """Sous-dossier de « 00 » d'un dossier à tri : hold / short / long (le plus profond qui correspond), sinon None."""
    sp = c.get("split")
    for seg in reversed(segs if sp else []):
        n = seg.lower()
        for k in ("hold", "short", "long"):
            if any(m in n for m in sp[k]["match"]):
                return k
    return None


def eff(cfg: dict, f: dict) -> dict:
    """Réglages d'un fichier : ceux de son dossier de compte, puis ceux de son sous-dossier de tri (compte TikTok…)."""
    c = dict(cfg_for(cfg, f["top"]))
    if f.get("sub"):
        c.update({k: v for k, v in c["split"][f["sub"]].items() if k in ("label", "tiktok", "youtube")})
    return c


def by_dur(f: dict, where: dict):
    """Spoufs (réglage « by_dur ») : le compte TikTok dépend de la durée du fichier, sans le déplacer.
    ≥ long → long_tiktok (@chadsciences, règle 1:01) ; < short (40 s), ou rallongé (« rallong » dans le nom) mais
    toujours < long, ou « (court) » dans le nom (original ≤ 50 k vues : pas rallongé) → short_tiktok (@axiomofficiel) ;
    entre les deux (40 s compris) → « à rallonger » (f["hold"] : pas encore postable sur TikTok ; Lou, 9 oct. ~23:15 :
    rallongé plus tard avec SA voix, jamais sans voix ; en attendant hors de « 00 »). Déjà sorti sur un TikTok →
    ce compte-là."""
    bd = f["acc"].get("by_dur")
    if not bd or f.get("sub"):
        return
    out = (where.get("tiktok") or [{}])[0].get("a")
    d = f.get("dur") or 0
    if out:
        f["acc"]["tiktok"] = out
    elif d >= bd["long"]:
        f["acc"]["tiktok"] = bd["long_tiktok"]
    elif d < bd["short"] or "rallong" in f["name"].lower() or "(court)" in f["name"].lower():
        f["acc"]["tiktok"] = bd["short_tiktok"]
    else:
        f["hold"] = True
    # Insta (Lou, 10 oct.) : les spoufs de plus d'1 min (ceux du TikTok @chadsciences) sont repostés sur l'Insta
    # @chadsciences, à la main ; l'Insta AXIOM (robot) ne prend que ceux de moins d'1 min, comme le TikTok @axiomofficiel
    if f["acc"]["tiktok"] == bd["long_tiktok"] and bd.get("long_instagram"):
        f["acc"]["instagram"], f["acc"]["ig_auto"] = bd["long_instagram"], False


def targets_of(c: dict) -> tuple:
    return tuple(p for p in TARGETS if not c or c.get(p) is not None) or TARGETS


def ig_read() -> set:
    """Les Insta où Lou poste à la main et que le robot lit (report_accounts.json « instagram », Business Discovery)."""
    p = STATE / "report_accounts.json"
    return set(json.loads(p.read_text(encoding="utf-8")).get("instagram") or []) if p.exists() else set()


def hand_targets(acc: dict, readable: set) -> tuple:
    """Où la vidéo doit encore sortir avant « 01 » : TikTok / YouTube, + l'Insta à la main s'il est lisible
    (Lou, 10 oct. : l'Insta @chadsciences est suivi par le robot, plus de coche à la main)."""
    ig = acc.get("instagram") if acc.get("ig_auto") is False else None
    return targets_of(acc) + (("instagram",) if ig and ig in readable else ())


def out_on(where: dict, p: str, acc: dict) -> bool:
    """Sortie sur la plateforme p — pour Instagram, sur LE compte visé (pas l'Insta AXIOM du robot)."""
    if p != "instagram":
        return bool(where.get(p))
    return any(x.get("a") == acc.get("instagram") for x in where.get(p) or [])


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
                segs = rel.rstrip("/").split("/")
                out.append({"id": k["id"], "name": name, "rel": rel + name, "top": top or "", "parent": fid,
                            "in_todo": TODO in segs, "waiting": TODO not in segs and any(w in s.lower() for s in segs for w in WAIT), "segs": segs[segs.index(TODO) + 1:] if TODO in segs else [],
                            "drive": (k.get("parentReference") or {}).get("driveId"),
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

    def titles(f):  # le nom du fichier, sa légende, et les titres des posts déjà reconnus pour ce fichier
        return [f["title"], (db.get("todo_caps", {}).get(f["id"]) or {}).get("text", "")] + [
            db["videos"][k].get("t", "") for k, m in pm.items() if m.get("f") == f["id"] and k in db["videos"]]

    now = time.time()
    for key, v in sorted(db["videos"].items(), key=lambda kv: -kv[1]["at"]):
        if v["p"] not in ("tiktok", "instagram", "youtube") or now - v["at"] > LOOKBACK:
            continue
        if v.get("r"):  # posté par le robot (quiz, ou repost dont on connaît déjà le fichier)
            continue
        if v["p"] == "youtube" and not v.get("dur"):  # sans durée, il faudrait comparer à tous les fichiers
            continue
        old = pm.get(key)
        if old and (old["f"] or now - v["at"] > RECHECK or (now - old["t"] < 6 * 3600 and old.get("v") == MATCH_V)):
            continue
        # un fichier rangé pour un compte (tri des spoufs) ne se reconnaît que sur ce compte : la copie « À rallonger »
        # d'un spouf posté sur @axiomofficiel n'est pas « sortie » pour ChadSciences
        cands = [f for f in files if f["dur"] and f["added"] <= v["at"] + 86400
                 and (not v.get("dur") or abs(f["dur"] - v["dur"]) <= DUR_TOL)
                 and (not f.get("sub") or (v["p"] in TARGETS and f["acc"].get(v["p"]) == v["a"]))]
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
                    near = None  # le plus proche, pour le journal quand rien ne passe
                    for f in cands:
                        if abs(f["dur"] - a["duration"]) > DUR_TOL:
                            continue
                        s = fpr.compare(a, fp_file(f))
                        if (s["image"] >= MIN_IMAGE and s["son"] >= MIN_SON) or \
                                (s["image"] >= SURE_IMAGE and s["son"] >= SURE_SON):
                            found = f["id"]
                            break
                        if not near or s["image"] > near[1]["image"]:
                            near = (f, s)
                    if not found and near:
                        log(f"sortie {key} : pas reconnue, la plus proche = {near[0]['rel']} "
                            f"(image {near[1]['image']}, son {near[1]['son']})")
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
        if not found and v["p"] in ("tiktok", "instagram"):
            # empreinte trop différente (autre export du même montage…) : même titre qu'un post déjà reconnu ailleurs
            # (souvent YouTube, reconnu par sa miniature) et même durée → même vidéo (9 oct. : « L'univers… » TikTok)
            for f in cands:
                if max((sim(v.get("t", ""), t) for t in titles(f) if t), default=0) >= 0.85:
                    found = f["id"]
                    break
        pm[key] = {"f": found, "t": now, "v": MATCH_V}
        if found:
            f = next(x for x in files if x["id"] == found)
            log(f"sortie {v['p']} @{v['a']} {v['id']} = {f['rel']}")
    # 2e passe, sans téléchargement : une sortie TikTok/Insta restée sans fichier prend celui d'une sortie reconnue
    # depuis avec le même titre et la même durée (10 oct. : TikTok « du chocolat » vu à 15:03 sans fichier, son YouTube
    # reconnu à 15:36 ; sans ça, plus revu avant 6 h et JARVIS lui collait une autre vidéo)
    for key, m in list(pm.items()):
        v = db["videos"].get(key)
        if m["f"] or not v or v["p"] not in ("tiktok", "instagram") or now - v["at"] > RECHECK:
            continue
        for f in files:
            if not f["dur"] or f["added"] > v["at"] + 86400 or (v.get("dur") and abs(f["dur"] - v["dur"]) > DUR_TOL) \
                    or (f.get("sub") and not (v["p"] in TARGETS and f["acc"].get(v["p"]) == v["a"])):
                continue
            posted = [db["videos"][k].get("t", "") for k, o in pm.items() if o.get("f") == f["id"] and k in db["videos"]]
            if max((sim(v.get("t", ""), t) for t in posted if t), default=0) >= 0.85:
                pm[key] = {"f": f["id"], "t": now}
                log(f"sortie {v['p']} @{v['a']} {v['id']} = {f['rel']} (même titre qu'une sortie reconnue)")
                break
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
        for key, pub in ig.items():   # spoufs postés par le robot directement depuis « 00 » (clé sp-<id du fichier>)
            if key.startswith("sp-"):
                add(key[3:], "instagram", {"a": "axiomscience.app", "at": _ts(pub["at"].replace(" ", "T") + "+02:00"),
                                           "url": pub.get("permalink")})
    for d in out.values():
        for p in d:
            d[p] = sorted({(x["a"], x["at"]): x for x in d[p]}.values(), key=lambda x: x["at"] or 0)
    return out


def _move(item_id: str, folder_id: str):
    od.call("PATCH", f"/me/drive/items/{item_id}", json={"parentReference": {"id": folder_id}}).raise_for_status()


def _copy(item_id: str, drive: str, folder_id: str, name: str):
    """Copie côté OneDrive (asynchrone : la copie apparaît au passage suivant). Déjà là (409) : rien à faire — sinon
    un déplacement raté après la copie bloquerait le tri à chaque passage."""
    r = od.call("POST", f"/me/drive/items/{item_id}/copy?@microsoft.graph.conflictBehavior=fail",
                json={"parentReference": {"driveId": drive, "id": folder_id}, "name": name})
    if r.status_code != 409:
        r.raise_for_status()


def sub_folder(base_id: str, sp: dict, key: str, create: bool = True) -> str | None:
    """Sous-dossier de tri dans « 00 » (ou « 01 ») : trouvé par mot-clé (Lou peut le renommer), sinon créé.
    « À rallonger » est DANS « ChadSciences (plus d'1 min) » (Lou : « dans chadsciences met aussi… »)."""
    parent = sub_folder(base_id, sp, "long", create) if key == "hold" else base_id
    if not parent:
        return None
    for c in od.children(parent):
        if "folder" in c and any(m in c["name"].lower() for m in sp[key]["match"]):
            return c["id"]
    return folder_child(parent, sp[key]["name"], create)


def probe_dur(item_id: str) -> float:
    """Durée lue par ffprobe sur le lien direct (quelques Ko lus, pas de téléchargement complet)."""
    r = subprocess.run([fpr.FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        od.download_url(item_id)], capture_output=True, text=True, timeout=120)
    return float(r.stdout.strip() or 0)


def fill_durations(db: dict, files: list[dict], log):
    """OneDrive ne donne pas toujours la durée (les spoufs livrés le 9 oct. : 0) ; sans elle, pas de tri ni de
    reconnaissance des sorties → ffprobe, une fois par version du fichier."""
    cache = db.setdefault("todo_dur", {})
    for f in files:
        if f["dur"]:
            continue
        c = cache.get(f["id"])
        if not c or c["ctag"] != f["ctag"]:
            try:
                c = cache[f["id"]] = {"ctag": f["ctag"], "dur": round(probe_dur(f["id"]), 3)}
            except Exception as ex:  # noqa: BLE001
                log(f"durée de {f['rel']} illisible ({ex})")
                continue
        f["dur"] = c["dur"]
    keep = {f["id"] for f in files}
    for k in [k for k in cache if k not in keep]:
        del cache[k]


def sort_split(cfg: dict, files: list[dict], tops: dict, log):
    """Tri par durée des dossiers à tri (voir l'en-tête), à chaque passage (rapide : aucun téléchargement)."""
    for f in files:
        sp = cfg_for(cfg, f["top"]).get("split") if f["in_todo"] and f["top"] else None
        if not sp or not f["dur"]:
            continue
        short = f["dur"] < sp["min"]
        if f["sub"] is None:                       # posée dans « 00 » sans compte
            key = "short" if short else "long"
        elif f["sub"] == "hold" and not short:     # rallongée : postable sur ChadSciences
            key = "long"
        else:
            continue
        try:
            base = folder_child(tops[f["top"]], TODO, create=True)
            dest = sub_folder(base, sp, key)
            if f["sub"] is None and short and sp.get("hold"):
                hold = sub_folder(base, sp, "hold")
                _copy(f["id"], f["drive"], hold, f["name"])
                if f["caption_id"]:
                    _copy(f["caption_id"], f["drive"], hold, f["caption_name"])
            _move(f["id"], dest)
            if f["caption_id"]:
                _move(f["caption_id"], dest)
            log(f"tri : {f['rel']} ({f['dur']:.0f} s) → {sp[key]['name']}"
                f"{' + copie « ' + sp['hold']['name'] + ' »' if f['sub'] is None and short and sp.get('hold') else ''}")
            f["sub"], f["parent"] = key, dest
        except Exception as ex:  # noqa: BLE001
            log(f"tri {f['rel']} impossible ({ex})")


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
    fill_durations(db, files, log)
    for f in files:
        f["sub"] = sub_of(cfg_for(cfg, f["top"]), f["segs"])
    sort_split(cfg, files, tops, log)
    for f in files:
        f["acc"] = eff(cfg, f)
    # à chaque passage : une vidéo postée par Lou est reconnue ET rangée (00 / 01) tout de suite — les passages
    # « meta » partent dans la minute où un post apparaît (veille de JARVIS, api/posts.js ; Lou, 10 oct. : « la synchro
    # entre les fichiers et les vidéos postées doit se faire chaque minute »)
    match_posts(db, files, work, ig_media or {}, log)
    posts = posts_of(db)
    finals = [f for f in files if f["in_todo"] or f["waiting"] or posts.get(f["id"])]
    hist = db.setdefault("todo_done", [])
    pending = []
    readable = ig_read()
    for f in finals:
        where = posts.get(f["id"], {})
        by_dur(f, where)
        targets = hand_targets(f["acc"], readable)
        if f["waiting"]:
            f["hold"] = True
            pending.append({**f, "posts": where})
            continue
        try:
            if all(out_on(where, p, f["acc"]) for p in targets) and f["top"]:
                dest = folder_child(tops[f["top"]], DONE, create=True)
                if f["sub"]:  # même tri dans « 01 » (deux versions d'un spouf portent le même nom)
                    dest = sub_folder(dest, f["acc"]["split"], "long" if f["sub"] == "hold" else f["sub"])
                _move(f["id"], dest)
                if f["caption_id"]:
                    _move(f["caption_id"], dest)
                log(f"sortie partout : {f['rel']} → « {DONE} »")
                hist.append({"id": f["id"], "title": f["title"], "folder": f["top"], "moved": int(time.time()),
                             "posts": where})
                continue
            if not f["in_todo"] and f["top"]:
                dest = folder_child(tops[f["top"]], TODO, create=True)
                _move(f["id"], dest)
                if f["caption_id"]:
                    _move(f["caption_id"], dest)
                log(f"sortie sur {', '.join(where)} : {f['rel']} → « {TODO} » (reste : "
                    f"{', '.join(p for p in targets if not out_on(where, p, f['acc']))})")
        except Exception as ex:  # noqa: BLE001
            log(f"à poster {f['rel']} : rangement impossible ({ex})")
        pending.append({**f, "posts": where})
    db["todo_done"] = [d for d in hist if time.time() - d["moved"] < 30 * 86400]
    hold = [i for i in pending if i["sub"] == "hold" or i.get("hold")]   # pas encore postables sur TikTok : à part
    pending = [i for i in pending if not (i["sub"] == "hold" or i.get("hold"))]
    # miniatures (une fois par fichier et par version) et légendes — aussi pour « à rallonger » : le robot Insta poste
    # les spoufs sans attendre TikTok (2 / jour), et JARVIS les montre dans son Planning
    ths, caps = db.setdefault("todo_th", {}), db.setdefault("todo_caps", {})
    up = []
    for it in pending + hold:
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
    keep = {i["id"] for i in pending + hold}
    db["todo_hold"] = [{"id": i["id"], "title": i["title"], "name": i["name"], "folder": i["top"], "sub": "hold",
                        "label": i["acc"].get("label"), "tiktok": i["acc"].get("tiktok"), "dur": round(i["dur"], 1),
                        "instagram": i["acc"].get("instagram"), "ig_auto": i["acc"].get("ig_auto"),
                        "th": (ths.get(i["id"]) or {}).get("url"), "caption": (caps.get(i["id"]) or {}).get("text"),
                        "posts": i["posts"], "added": i["added"], "web": i["web"], "waiting": i["waiting"]} for i in hold]
    for d in (ths, caps):
        for k in [k for k in d if k not in keep]:
            del d[k]
    db["todo"] = [{"id": i["id"], "title": i["title"], "name": i["name"], "folder": i["top"], "sub": i["sub"],
                   "label": i["acc"].get("label") or i["top"] or ACQ,
                   "tiktok": i["acc"].get("tiktok"), "youtube": i["acc"].get("youtube"),
                   # Insta (Lou, 9 oct.) : originaux ChadSciences → @chadsciences à la main (le robot ne peut pas le lire) ;
                   # le reste → @axiomscience.app, reposté seul par le robot après la sortie TikTok
                   "instagram": i["acc"].get("instagram"), "ig_auto": i["acc"].get("ig_auto"),
                   "dur": round(i["dur"], 1), "added": i["added"], "web": i["web"], "th": cover(db, i) or (ths.get(i["id"]) or {}).get("url"),
                   "caption": (caps.get(i["id"]) or {}).get("text"), "posts": i["posts"],
                   "targets": list(targets_of(i["acc"]))} for i in pending]
    log(f"à poster : {len(db['todo'])} vidéo(s) finie(s) en attente, {len(db['todo_hold'])} à rallonger")
