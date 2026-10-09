"""Les vidéos finies, à poster à la main sur TikTok (Lou, 9 oct. 2026).

Dans chaque dossier de compte de OneDrive « AXIOM acquisition » (Octave, Théo Rème, Singe explique DA, Spouf…) :
  « 00 - Vidéos finales à poster »  les vidéos que Lou a validées : il ne reste qu'à les poster ;
  « 01 - Déjà postées »             le robot y range une vidéo dès qu'il la voit en ligne sur un compte TikTok
                                    (même empreinte image + son que pour les reposts Insta), au relevé du matin.
Une vidéo = « NN - Titre.mp4 », sa légende à coller = « NN - Titre - légende.txt » (ou « NN - Titre.txt »).
JARVIS les affiche en tête du Rapport : miniature, durée, depuis quand elle attend, légende à copier, lien OneDrive.
Réglages par dossier : state/todo_accounts.json (nom affiché, compte TikTok visé).
"""
from __future__ import annotations

import hashlib
import json
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
DUR_TOL = 2.0
MIN_IMAGE, MIN_SON = 0.75, 0.85


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) if iso else 0


def scan() -> list[dict]:
    """Les vidéos à poster de chaque dossier de compte, avec leur légende."""
    root = od.by_path(ACQ)
    out = []
    for acc in od.children(root["id"]):
        if "folder" not in acc or acc["name"].startswith("_"):
            continue
        sub = {c["name"]: c for c in od.children(acc["id"]) if "folder" in c}
        if TODO not in sub:
            continue
        files = od.children(sub[TODO]["id"])
        txt = {f["name"]: f for f in files if f["name"].lower().endswith(".txt")}
        for f in files:
            if not f["name"].lower().endswith((".mp4", ".mov")):
                continue
            stem = f["name"].rsplit(".", 1)[0]
            cap = txt.get(f"{stem} - légende.txt") or txt.get(f"{stem}.txt")
            out.append({"id": f["id"], "name": f["name"], "title": stem.split(" - ", 1)[-1] if " - " in stem else stem,
                        "folder": acc["name"], "done_folder": (sub.get(DONE) or {}).get("id"), "todo_folder": sub[TODO]["id"],
                        "dur": ((f.get("video") or {}).get("duration") or 0) / 1000, "added": _ts(f.get("createdDateTime")),
                        "web": f.get("webUrl"), "ctag": f.get("cTag"), "caption_id": cap["id"] if cap else None,
                        "caption_name": cap["name"] if cap else None})
    return out


def _move(item_id: str, folder_id: str):
    r = od.call("PATCH", f"/me/drive/items/{item_id}", json={"parentReference": {"id": folder_id}})
    r.raise_for_status()


def detect(db: dict, items: list[dict], work: Path, log) -> list[dict]:
    """Repère les vidéos à poster déjà en ligne sur TikTok ; les range dans « 01 - Déjà postées »."""
    checked = db.setdefault("todo_checked", {})   # "item|tiktok" → déjà comparés
    reposts = {}
    rp = Path(__file__).resolve().parents[1] / "state" / "reposts.json"
    if rp.exists():  # les reposts Insta ont déjà retrouvé leur fichier OneDrive
        for v in json.loads(rp.read_text(encoding="utf-8")).get("videos", {}).values():
            if v.get("source") == "onedrive" and v.get("item_id"):
                reposts[v["item_id"]] = v
    fps: dict[str, dict] = {}
    posted = []
    for it in items:
        hit = None
        if it["id"] in reposts:
            r = reposts[it["id"]]
            hit = {"tt": r["id"], "a": r["account"], "at": r["posted"]}
        else:
            for v in db["videos"].values():
                if v["p"] != "tiktok" or v["at"] < it["added"] - 2 * 86400 or not v.get("dur") or abs(v["dur"] - it["dur"]) > DUR_TOL:
                    continue
                key = f"{it['id']}|{v['id']}"
                if key in checked:
                    continue
                try:
                    if it["id"] not in fps:
                        fps[it["id"]] = fpr.compute(od.download(it["id"], work / "todo" / f"{it['id']}.mp4"))
                    small = tiktok.download(v["url"], work / "tt" / v["id"], best=False)
                    s = fpr.compare(fps[it["id"]], fpr.compute(small))
                except Exception as ex:  # noqa: BLE001
                    log(f"à poster {it['name']} : comparaison impossible ({ex})")
                    continue
                checked[key] = s
                if s["image"] >= MIN_IMAGE and s["son"] >= MIN_SON:
                    hit = {"tt": v["id"], "a": v["a"], "at": v["at"], "url": v.get("url")}
                    break
        if not hit:
            continue
        try:
            if it["done_folder"]:
                _move(it["id"], it["done_folder"])
                if it["caption_id"]:
                    _move(it["caption_id"], it["done_folder"])
        except Exception as ex:  # noqa: BLE001
            log(f"à poster {it['name']} : rangement impossible ({ex})")
            continue
        log(f"postée : {it['folder']} / {it['name']} → @{hit['a']} {hit['tt']} — rangée dans « {DONE} »")
        posted.append({**it, **hit, "moved": int(time.time())})
    return posted


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


def run(db: dict, work: Path, full: bool, blob_put, log) -> None:
    """Met à jour db["todo"] (à poster) et db["todo_done"] (rangées par le robot)."""
    work.mkdir(parents=True, exist_ok=True)
    cfg_p = Path(__file__).resolve().parents[1] / "state" / "todo_accounts.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else {}
    items = scan()
    if full:
        done = detect(db, items, work, log)
        ids = {d["id"] for d in done}
        items = [i for i in items if i["id"] not in ids]
        hist = db.setdefault("todo_done", [])
        hist += [{k: d.get(k) for k in ("id", "name", "title", "folder", "dur", "tt", "a", "at", "url", "moved")} for d in done]
        db["todo_done"] = [d for d in hist if time.time() - d["moved"] < 30 * 86400]
    # miniatures (une fois par fichier et par version), légendes
    ths = db.setdefault("todo_th", {})
    up = []
    for it in items:
        if ths.get(it["id"], {}).get("ctag") != it["ctag"]:
            try:
                f = thumb(it, work)
                if f:
                    up.append((f, f"jarvis/todo/{it['id'].replace('!', '_')}-{hashlib.md5(str(it['ctag']).encode()).hexdigest()[:8]}.jpg", "image/jpeg", 31536000, it))
            except Exception as ex:  # noqa: BLE001
                log(f"à poster {it['name']} : miniature impossible ({ex})")
    if up:
        urls = blob_put([(f, p, ct, age) for f, p, ct, age, _ in up])
        for f, p, ct, age, it in up:
            if p in urls:
                ths[it["id"]] = {"ctag": it["ctag"], "url": urls[p]}
    caps = db.setdefault("todo_caps", {})
    for it in items:
        if it["caption_id"] and caps.get(it["id"], {}).get("name") != it["caption_name"] + str(it["ctag"]):
            try:
                caps[it["id"]] = {"name": it["caption_name"] + str(it["ctag"]), "text": od.read_text(it["caption_id"]).strip()}
            except Exception:  # noqa: BLE001
                pass
    keep = {i["id"] for i in items}
    chk = db.get("todo_checked", {})
    for k in [k for k in chk if k.split("|")[0] not in keep]:
        del chk[k]
    for d in (ths, caps):
        for k in [k for k in d if k not in keep]:
            del d[k]
    db["todo"] = [{"id": i["id"], "title": i["title"], "name": i["name"], "folder": i["folder"],
                   "label": (cfg.get(i["folder"]) or {}).get("label") or i["folder"],
                   "tiktok": (cfg.get(i["folder"]) or {}).get("tiktok"), "dur": round(i["dur"], 1), "added": i["added"],
                   "web": i["web"], "th": (ths.get(i["id"]) or {}).get("url"),
                   "caption": (caps.get(i["id"]) or {}).get("text")} for i in items]
    log(f"à poster : {len(db['todo'])} vidéo(s) finie(s) en attente")
