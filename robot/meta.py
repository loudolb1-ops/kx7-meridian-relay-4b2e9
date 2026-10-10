"""Publication sur Instagram @axiomscience.app et sur la Page Facebook « AXIOM - Maths & Sciences » (Graph v25.0).

Instagram : par LIEN (video_url) — avec le jeton de Page, l'envoi direct du fichier est refusé (testé le 8 oct.) ;
le lien = le lien direct OneDrive du fichier. Facebook : envoi direct du fichier (rupload), qui marche.
Jetons dans les secrets GitHub : IG_TOKEN (= jeton de Page, n'expire jamais), IG_USER_ID, FB_TOKEN, FB_PAGE_ID.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

GRAPH = "https://graph.facebook.com/v25.0"
PARIS = ZoneInfo("Europe/Paris")  # heure des posts = heure de Paris (le serveur GitHub est en UTC)
RUPLOAD = "https://rupload.facebook.com/video-upload/v25.0"


def _env(k: str) -> str:
    v = os.environ.get(k, "").strip()
    if not v:
        raise RuntimeError(f"secret {k} manquant")
    return v


def instagram(video_url: str, caption: str, thumb_ms: int | None = None, dry: bool = False) -> dict:
    """dry=True : Instagram télécharge et traite la vidéo mais elle n'est PAS publiée (le brouillon expire seul)."""
    tok, uid = _env("IG_TOKEN"), _env("IG_USER_ID")
    data = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true",
            "access_token": tok}
    if thumb_ms is not None:
        data["thumb_offset"] = str(thumb_ms)
    r = requests.post(f"{GRAPH}/{uid}/media", data=data, timeout=60)
    if not r.ok:
        raise RuntimeError(f"création du conteneur refusée : {r.status_code} {r.text[:500]}")
    cid = r.json()["id"]
    status = ""
    for _ in range(90):
        s = requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status", "access_token": tok}, timeout=30)
        status = s.json().get("status_code", "")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"traitement Instagram en échec : {s.text[:500]}")
        time.sleep(10)
    else:
        raise RuntimeError(f"traitement trop long (dernier état {status})")
    if dry:
        return {"dry_run": True, "container": cid, "status": status}
    p = requests.post(f"{GRAPH}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=60)
    if not p.ok:
        raise RuntimeError(f"publication refusée : {p.status_code} {p.text[:500]}")
    mid = p.json()["id"]
    rec = {"media_id": mid, "permalink": None, "at": datetime.now(PARIS).strftime("%Y-%m-%d %H:%M:%S")}
    try:  # publiée : plus rien ne doit lever d'erreur (sinon le créneau suivant la reposterait)
        rec["permalink"] = requests.get(f"{GRAPH}/{mid}", params={"fields": "permalink", "access_token": tok},
                                        timeout=30).json().get("permalink")
    except Exception:  # noqa: BLE001
        pass
    return rec


def _fb_status(tok: str, vid: str) -> dict:
    r = requests.get(f"{GRAPH}/{vid}", params={"fields": "status,permalink_url", "access_token": tok}, timeout=30)
    return r.json() if r.ok else {"error": r.text[:300]}


class RateLimited(RuntimeError):
    """Facebook freine (« We limit how often you can post ») : ni la vidéo ni le jeton n'y sont pour rien."""


def facebook(video: Path, caption: str, dry: bool = False, accepted=None, when: datetime | None = None) -> dict:
    """accepted(rec) est appelé dès que Facebook accepte la publication : on l'inscrit avant d'attendre le traitement,
    pour ne jamais la reposter si la suite échoue. when = programmée nativement sur la Page à cette heure-là (visible
    dans Meta Business Suite → Planifié), au lieu d'être publiée tout de suite."""
    tok, pid = _env("FB_TOKEN"), _env("FB_PAGE_ID")
    r = requests.post(f"{GRAPH}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok}, timeout=60)
    if not r.ok:
        if "limit how often" in r.text or '"code":368' in r.text:
            raise RateLimited(f"Facebook freine les envois : {r.text[:200]}")
        raise RuntimeError(f"ouverture de l'envoi refusée : {r.status_code} {r.text[:500]}")
    vid = r.json()["video_id"]
    with open(video, "rb") as fh:
        u = requests.post(f"{RUPLOAD}/{vid}", data=fh, timeout=900,
                          headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(video.stat().st_size)})
    if not (u.ok and u.json().get("success")):
        raise RuntimeError(f"envoi refusé : {u.status_code} {u.text[:300]}")
    if dry:
        return {"dry_run": True, "video_id": vid}
    data = {"upload_phase": "finish", "video_id": vid, "description": caption, "access_token": tok,
            "video_state": "SCHEDULED" if when else "PUBLISHED"}
    if when:
        data["scheduled_publish_time"] = str(int(when.timestamp()))
    f = requests.post(f"{GRAPH}/{pid}/video_reels", timeout=120, data=data)
    if not (f.ok and f.json().get("success")):
        raise RuntimeError(f"publication refusée : {f.status_code} {f.text[:500]}")
    rec = {"video_id": vid, "at": (when or datetime.now(PARIS)).astimezone(PARIS).strftime("%Y-%m-%d %H:%M:%S"),
           "state": "programmée" if when else "en traitement"}
    if accepted:
        accepted(rec)
    if when:
        return rec  # elle sortira toute seule à l'heure dite
    st = {}
    for _ in range(30):  # le verdict arrive en quelques minutes ; au-delà, la vidéo finit sa mise en ligne seule
        st = _fb_status(tok, vid)
        s = st.get("status", {})
        if s.get("video_status") == "error" or s.get("processing_phase", {}).get("status") == "error":
            raise RuntimeError(f"traitement Facebook en échec : {str(s)[:400]}")
        pub = s.get("publishing_phase", {})
        if pub.get("status") == "complete" or pub.get("publish_status") == "published":
            rec["state"] = "published"
            break
        time.sleep(10)
    link = st.get("permalink_url")
    rec["permalink"] = ("https://www.facebook.com" + link) if link and link.startswith("/") else link
    return rec
