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


class AccountDown(RuntimeError):
    """Le compte Instagram lui-même est injoignable (plus relié à la Page Facebook, 12 oct. 2026 : « Object with ID …
    does not exist ») : ce n'est pas la faute de la vidéo, elle ne doit pas perdre un de ses 3 essais."""


def instagram(video_url: str, caption: str, thumb_ms: int | None = None, dry: bool = False,
              cover_url: str | None = None) -> dict:
    """dry=True : Instagram télécharge et traite la vidéo mais elle n'est PAS publiée (le brouillon expire seul).
    cover_url = la miniature (JPEG) en couverture du reel (Lou, 14 oct.) ; refusée → la vidéo part sans."""
    tok, uid = _env("IG_TOKEN"), _env("IG_USER_ID")
    data = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true",
            "access_token": tok}
    if thumb_ms is not None:
        data["thumb_offset"] = str(thumb_ms)
    cover = None
    if cover_url:   # repli sans couverture seulement AVANT media_publish : jamais deux publications
        try:
            cid, status = _ig_container(tok, uid, {**data, "cover_url": cover_url})
            cover = "ok"
        except AccountDown:
            raise
        except Exception as ex:  # noqa: BLE001
            cover = f"refusée ({str(ex)[:160]})"
            cid, status = _ig_container(tok, uid, data)
    else:
        cid, status = _ig_container(tok, uid, data)
    extra = {"cover": cover} if cover else {}
    if dry:
        return {"dry_run": True, "container": cid, "status": status, **extra}
    p = requests.post(f"{GRAPH}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=60)
    if not p.ok:
        raise RuntimeError(f"publication refusée : {p.status_code} {p.text[:500]}")
    mid = p.json()["id"]
    rec = {"media_id": mid, "permalink": None, "at": datetime.now(PARIS).strftime("%Y-%m-%d %H:%M:%S"), **extra}
    try:  # publiée : plus rien ne doit lever d'erreur (sinon le créneau suivant la reposterait)
        rec["permalink"] = requests.get(f"{GRAPH}/{mid}", params={"fields": "permalink", "access_token": tok},
                                        timeout=30).json().get("permalink")
    except Exception:  # noqa: BLE001
        pass
    return rec


def _ig_container(tok: str, uid: str, data: dict) -> tuple[str, str]:
    """Conteneur créé et traité par Instagram (FINISHED) : (id, état)."""
    r = requests.post(f"{GRAPH}/{uid}/media", data=data, timeout=60)
    if not r.ok and uid in r.text and '"error_subcode":33' in r.text:
        raise AccountDown(f"compte Instagram {uid} injoignable avec le jeton (plus relié à la Page Facebook ?) : "
                          f"{r.status_code} {r.text[:200]}")
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
    return cid, status


def _fb_status(tok: str, vid: str) -> dict:
    r = requests.get(f"{GRAPH}/{vid}", params={"fields": "status,permalink_url", "access_token": tok}, timeout=30)
    return r.json() if r.ok else {"error": r.text[:300]}


def _unix(v) -> int:
    """Heure renvoyée par Graph : secondes Unix ou ISO (« 2026-10-14T15:30:00+0000 »)."""
    v = str(v)
    return int(v) if v.isdigit() else int(datetime.strptime(v, "%Y-%m-%dT%H:%M:%S%z").timestamp())


def fb_reschedule(vid: str, when: datetime):
    """Change l'heure d'une vidéo déjà programmée sur la Page (pause du planning, 13 oct.), puis relit l'heure."""
    tok = _env("FB_TOKEN")
    ts = int(when.timestamp())
    errs = []
    for extra in ({}, {"published": "false"}):
        r = requests.post(f"{GRAPH}/{vid}", timeout=60, data={"scheduled_publish_time": str(ts), "access_token": tok, **extra})
        if r.ok:
            g = requests.get(f"{GRAPH}/{vid}", params={"fields": "scheduled_publish_time", "access_token": tok}, timeout=30)
            got = g.json().get("scheduled_publish_time") if g.ok else None
            if got is None or abs(_unix(got) - ts) < 120:
                return
            errs.append(f"heure relue {got}")
        else:
            errs.append(f"{r.status_code} {r.text[:300]}")
    raise RuntimeError("reprogrammation refusée : " + " | ".join(errs))


class RateLimited(RuntimeError):
    """Facebook freine (« We limit how often you can post ») : ni la vidéo ni le jeton n'y sont pour rien."""


def _fb_thumb(tok: str, vid: str, cover: Path) -> str:
    """Miniature personnalisée de la vidéo (« ok » ou la raison du refus) ; ne lève jamais."""
    try:
        with open(cover, "rb") as fh:
            r = requests.post(f"{GRAPH}/{vid}/thumbnails", timeout=120, files={"source": (cover.name, fh, "image/jpeg")},
                              data={"is_preferred": "true", "access_token": tok})
        return "ok" if r.ok else f"refusée ({r.status_code} {r.text[:160]})"
    except Exception as ex:  # noqa: BLE001
        return f"refusée ({str(ex)[:160]})"


def facebook(video: Path, caption: str, dry: bool = False, accepted=None, when: datetime | None = None,
             cover: Path | None = None) -> dict:
    """accepted(rec) est appelé dès que Facebook accepte la publication : on l'inscrit avant d'attendre le traitement,
    pour ne jamais la reposter si la suite échoue. when = programmée nativement sur la Page à cette heure-là (visible
    dans Meta Business Suite → Planifié), au lieu d'être publiée tout de suite. cover = la miniature (JPEG) en
    vignette (Lou, 14 oct.) : posée avant la mise en ligne, sinon juste après ; refusée → la vidéo part sans."""
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
    thumb = _fb_thumb(tok, vid, cover) if cover else None
    if dry:
        return {"dry_run": True, "video_id": vid, **({"cover": thumb} if thumb else {})}
    data = {"upload_phase": "finish", "video_id": vid, "description": caption, "access_token": tok,
            "video_state": "SCHEDULED" if when else "PUBLISHED"}
    if when:
        data["scheduled_publish_time"] = str(int(when.timestamp()))
    f = requests.post(f"{GRAPH}/{pid}/video_reels", timeout=120, data=data)
    if not (f.ok and f.json().get("success")):
        raise RuntimeError(f"publication refusée : {f.status_code} {f.text[:500]}")
    rec = {"video_id": vid, "at": (when or datetime.now(PARIS)).astimezone(PARIS).strftime("%Y-%m-%d %H:%M:%S"),
           "state": "programmée" if when else "en traitement"}
    if thumb and thumb != "ok":   # refusée avant la mise en ligne : on retente sur la vidéo acceptée
        thumb = _fb_thumb(tok, vid, cover) + " (après)"
    if thumb:
        rec["cover"] = thumb
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
