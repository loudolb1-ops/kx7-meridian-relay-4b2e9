"""Le rapport de JARVIS : tout ce qui est publié sur les comptes de Lou (TikTok, Instagram, Facebook), les stats de
chaque vidéo, et la santé du robot (4 posts par jour sur Instagram et sur Facebook). Lou, 9 oct. 2026 : « m'assurer
que le postage automatique fonctionne + un récap des stats de toutes les vidéos, pour ne plus aller voir sur
TikTok, Insta et Facebook ».

python robot/report.py            rapport complet (une fois par jour, vers 06:30) : TikTok + Instagram + Facebook
python robot/report.py --meta     après chaque créneau, et dans la minute où un post de Lou apparaît (mode « meta » du
                                  workflow, lancé par la veille de JARVIS) : Instagram + Facebook, vidéos TikTok / YouTube
                                  de Lou reconnues (Lou, 9 oct. : « c'est pas à moi de valider ») et rangées 00 / 01
python robot/report.py --local    écrit work/report.json sans rien envoyer (essai)

Base : state/report_db.json (toutes les vidéos connues + un relevé de stats par jour). Comptes : state/report_accounts.json.
Sortie : jarvis/report.json dans le Blob PRIVÉ « jarvis-prive » (servi par JARVIS /api/report, connexion requise)
+ vignettes 9:16 dans le stockage Vercel Blob public « jarvis-rapport ».
Stats d'une vidéo = [vues, likes, commentaires, partages, enregistrements] (None = la plateforme ne le donne pas).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import fingerprint as fpr  # noqa: E402  (chemins ffmpeg)
import slots  # noqa: E402
import tiktok  # noqa: E402
import todo  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
WORK = ROOT / "work" / "report"
TZ = slots.TZ
GRAPH = "https://graph.facebook.com/v25.0"
FRESH_DAYS = 45      # stats relevées chaque jour pour les vidéos de moins de 45 jours ; les plus vieilles, le lundi
KEEP_SNAPS = 60      # jours de relevés gardés par vidéo
UA = {"User-Agent": tiktok.UA}

META_ONLY = "--meta" in sys.argv
IG_MEDIA: dict[str, str] = {}
LOCAL = "--local" in sys.argv


def jload(name: str, default):
    p = STATE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def jsave(name: str, data):
    (STATE / name).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def ensure_targets(cfg: dict) -> dict:
    """Chaque compte TikTok / YouTube visé par un dossier OneDrive (todo_accounts.json) est relevé, sans qu'on ait à
    penser à l'ajouter ici aussi : un compte cible absent de report_accounts.json n'était jamais lu, sa vidéo restait
    « à poster » pour toujours (YouTube @octave.explique, 10 oct.). L'id de chaîne YouTube est lu sur sa page @handle.
    report_accounts.json est aussi la liste que lit la veille de JARVIS (api/posts.js) : elle suit toute seule."""
    added = []
    tt, yt, order = cfg.setdefault("tiktok", []), cfg.setdefault("youtube", {}), cfg.setdefault("order", [])
    for acc in jload("todo_accounts.json", {}).values():
        h = acc.get("tiktok")
        if h and h not in tt:
            tt.append(h)
            added.append(f"tiktok:{h}")
        h = acc.get("youtube")
        if h and h not in yt:
            try:
                page = requests.get(f"https://www.youtube.com/@{h}", headers={**UA, "Cookie": "SOCS=CAI; CONSENT=YES+"}, timeout=40).text
                cid = re.search(r'"externalId":"(UC[\w-]{22})"', page)  # sans le cookie : page de consentement
            except Exception as ex:  # noqa: BLE001
                log(f"YouTube @{h} : chaîne cible non relevée, id introuvable ({ex})")
                continue
            if not cid:
                log(f"YouTube @{h} : chaîne cible non relevée, id introuvable sur sa page")
                continue
            yt[h] = cid.group(1)
            added.append(f"youtube:{h}")
    if added:
        order += [k for k in added if k not in order]
        (STATE / "report_accounts.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        log(f"comptes cibles ajoutés au relevé : {', '.join(added)}")
    return cfg


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def log(msg: str):
    print(f"{datetime.now(TZ):%H:%M:%S}  {msg}", flush=True)


def clean(text: str, n: int = 140) -> str:
    """Titre lisible : sans hashtags ni mentions, sans les lignes « AXIOM lien en bio » / hashtags du robot."""
    t = (text or "").split("\n\n")[0]
    t = re.sub(r"[#@]\S+", "", t)
    t = re.sub(r"\s*AXIOM lien en bio\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–|·")
    return t[:n]


def snap(v: dict, stats: list):
    v.setdefault("snaps", {})[today()] = stats
    for d in sorted(v["snaps"])[:-KEEP_SNAPS]:
        del v["snaps"][d]


# ------------------------------------------------------------------ TikTok

def tiktok_account(db: dict, handle: str):
    acc = db["accounts"].setdefault(f"tiktok:{handle}", {"p": "tiktok", "a": handle})
    try:
        r = requests.get(f"https://www.tiktok.com/@{handle}", headers={**UA, "Accept-Language": "fr-FR"}, timeout=40)
        m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', r.text, re.S)
        info = (json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("userInfo")
                if m else None)
        if info and info.get("user", {}).get("id"):
            u, s = info["user"], info.get("stats", {})
            acc.update(name=u.get("nickname"), url=f"https://www.tiktok.com/@{handle}", avatar_src=u.get("avatarThumb"),
                       followers=s.get("followerCount"), likes=s.get("heartCount"), count=s.get("videoCount"), found=True)
            acc.setdefault("fsnaps", {})[today()] = s.get("followerCount")
        elif info is None and r.status_code == 200 and "statusCode" in r.text:
            acc["found"] = False
    except Exception as ex:  # noqa: BLE001
        log(f"TikTok @{handle} profil : {ex}")
    try:
        latest = tiktok.latest(handle)
    except Exception as ex:  # noqa: BLE001
        log(f"TikTok @{handle} liste : {ex}")
        return
    if latest is None:
        acc["found"] = False
        return
    for it in latest:
        v = db["videos"].setdefault(f"tt:{it['id']}", {"p": "tiktok", "a": handle, "id": it["id"],
                                                        "url": f"https://www.tiktok.com/@{handle}/video/{it['id']}",
                                                        "at": it["posted"]})
        v["t"] = clean(it["desc"]) or v.get("t", "")


def tiktok_stats(db: dict):
    """Vues, likes, commentaires, partages, enregistrements de chaque vidéo (yt-dlp, une vidéo à la fois)."""
    import yt_dlp
    monday = datetime.now(TZ).weekday() == 0
    todo = [v for v in db["videos"].values() if v["p"] == "tiktok" and (not v.get("th") or (
            today() not in v.get("snaps", {})
            and (time.time() - v["at"] < FRESH_DAYS * 86400 or monday or not v.get("snaps"))))]
    log(f"TikTok : stats de {len(todo)} vidéo(s)")

    def one(v):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
                d = y.extract_info(v["url"], download=False)
        except Exception as ex:  # noqa: BLE001
            return v, None, str(ex)[:160]
        return v, d, None

    with ThreadPoolExecutor(4) as pool:
        for v, d, err in pool.map(one, todo):
            if err:
                v["err"] = err
                continue
            v.pop("err", None)
            snap(v, [d.get("view_count"), d.get("like_count"), d.get("comment_count"), d.get("repost_count"),
                     d.get("save_count")])
            v["dur"] = d.get("duration") or v.get("dur")
            v["t"] = v.get("t") or clean(d.get("description") or d.get("title") or "")
            if not v.get("thumb_src") or not v.get("th"):
                v["thumb_src"] = d.get("thumbnail")


# ------------------------------------------------------------------ Instagram / Facebook

def _get(url: str, params: dict) -> dict:
    r = requests.get(url, params=params, timeout=40)
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:200]}")
    return r.json()


def ig_scopes(tok: str) -> list:
    """Les permissions du jeton (journal) : sans instagram_manage_insights, pas de vues Instagram."""
    try:
        return _get(f"{GRAPH}/debug_token", {"input_token": tok, "access_token": tok}).get("data", {}).get("scopes") or []
    except Exception as ex:  # noqa: BLE001
        return [f"? {str(ex)[:80]}"]


def instagram(db: dict, robot: dict):
    tok, uid = os.environ.get("IG_TOKEN"), os.environ.get("IG_USER_ID")
    if not tok:
        return
    me = _get(f"{GRAPH}/{uid}", {"fields": "username,followers_count,media_count,profile_picture_url", "access_token": tok})
    acc = db["accounts"].setdefault(f"instagram:{me['username']}", {"p": "instagram", "a": me["username"]})
    acc.update(name=me["username"], url=f"https://www.instagram.com/{me['username']}/", followers=me.get("followers_count"),
               count=me.get("media_count"), avatar_src=me.get("profile_picture_url"), found=True)
    acc.setdefault("fsnaps", {})[today()] = me.get("followers_count")
    media = _get(f"{GRAPH}/{uid}/media", {"fields": "id,caption,media_type,media_product_type,timestamp,like_count,"
                                          "comments_count,permalink,thumbnail_url,media_url", "limit": 60,
                                          "access_token": tok}).get("data", [])
    # retentées à chaque passage (avant le 10 oct., un premier refus les coupait pour toujours, même après l'ajout
    # de la permission au jeton)
    scopes = ig_scopes(tok)
    known = scopes and not scopes[0].startswith("?")
    insights = "instagram_manage_insights" in scopes or not known
    log(f"Instagram : jeton {', '.join(scopes) or '?'}")
    db.setdefault("perms", {})
    for m in media:
        at = int(datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z").timestamp())
        v = db["videos"].setdefault(f"ig:{m['id']}", {"p": "instagram", "a": me["username"], "id": m["id"], "at": at})
        if m.get("media_type") == "VIDEO" and m.get("media_url"):
            IG_MEDIA[m["id"]] = m["media_url"]  # lien du fichier (signé, valable ce passage) : empreinte des « à poster »
        v.update(url=m.get("permalink"), t=clean(m.get("caption") or ""), kind=m.get("media_type"),
                 thumb_src=v.get("thumb_src") or m.get("thumbnail_url") or m.get("media_url"))
        views = shares = saves = None
        if insights and m.get("media_type") == "VIDEO" and time.time() - at < FRESH_DAYS * 86400:
            r = requests.get(f"{GRAPH}/{m['id']}/insights", timeout=30,
                             params={"metric": "views,shares,saved", "access_token": tok})
            if r.ok:
                vals = {x["name"]: (x.get("values") or [{}])[0].get("value") for x in r.json().get("data", [])}
                views, shares, saves = vals.get("views"), vals.get("shares"), vals.get("saved")
            elif "permission" in r.text:
                insights = False  # le jeton n'a pas instagram_manage_insights : vues indisponibles
        old = v.get("snaps", {}).get(today()) or [None] * 5
        snap(v, [views if views is not None else old[0], m.get("like_count"), m.get("comments_count"),
                 shares if shares is not None else old[3], saves if saves is not None else old[4]])
        v["r"] = robot["ig"].get(m["id"])
    db["perms"]["ig_insights"] = insights


def instagram_other(db: dict, handle: str):
    """Un Insta pro qui n'est pas celui du robot (@chadsciences, où Lou poste à la main) : lu via Business Discovery
    depuis le jeton d'@axiomscience.app. Ses posts sont reconnus comme les autres (match_posts) : plus de coche à la main
    dans JARVIS (Lou, 10 oct. : « c'est le bot qui détecte si une vidéo a été postée ou pas »)."""
    tok, uid = os.environ.get("IG_TOKEN"), os.environ.get("IG_USER_ID")
    if not tok:
        return
    media_f = "id,caption,media_type,media_product_type,timestamp,like_count,comments_count,permalink,media_url"
    for extra in (",view_count", ""):   # view_count (Reels) pas toujours servi par Business Discovery
        r = requests.get(f"{GRAPH}/{uid}", timeout=40, params={
            "fields": f"business_discovery.username({handle}){{username,followers_count,media_count,profile_picture_url,"
                      f"media.limit(40){{{media_f}{extra}}}}}", "access_token": tok})
        if r.ok or not extra:
            break
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:200]}")
    bd = r.json()["business_discovery"]
    acc = db["accounts"].setdefault(f"instagram:{handle}", {"p": "instagram", "a": handle})
    acc.update(name=bd.get("username") or handle, url=f"https://www.instagram.com/{handle}/", followers=bd.get("followers_count"),
               count=bd.get("media_count"), avatar_src=bd.get("profile_picture_url"), found=True)
    acc.setdefault("fsnaps", {})[today()] = bd.get("followers_count")
    media = (bd.get("media") or {}).get("data", [])
    for m in media:
        at = int(datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z").timestamp())
        v = db["videos"].setdefault(f"ig:{m['id']}", {"p": "instagram", "a": handle, "id": m["id"], "at": at})
        if m.get("media_type") == "VIDEO" and m.get("media_url"):
            IG_MEDIA[m["id"]] = m["media_url"]
        v.update(url=m.get("permalink"), t=clean(m.get("caption") or ""), kind=m.get("media_type"),
                 thumb_src=v.get("thumb_src") or m.get("media_url"))
        old = v.get("snaps", {}).get(today()) or [None] * 5
        snap(v, [m.get("view_count") if m.get("view_count") is not None else old[0], m.get("like_count"),
                 m.get("comments_count"), old[3], old[4]])
    log(f"Instagram @{handle} : {len(media)} posts lus")


def facebook(db: dict, robot: dict):
    tok, pid = os.environ.get("FB_TOKEN"), os.environ.get("FB_PAGE_ID")
    if not tok:
        return
    page = _get(f"{GRAPH}/{pid}", {"fields": "name,followers_count,picture{url}", "access_token": tok})
    acc = db["accounts"].setdefault(f"facebook:{pid}", {"p": "facebook", "a": page["name"]})
    acc.update(name=page["name"], url=f"https://www.facebook.com/{pid}", followers=page.get("followers_count"),
               avatar_src=(page.get("picture") or {}).get("data", {}).get("url"), found=True)
    acc.setdefault("fsnaps", {})[today()] = page.get("followers_count")
    reels, url = [], f"{GRAPH}/{pid}/video_reels"
    params = {"fields": "id,description,created_time,permalink_url,length,views,picture,thumbnails.limit(1){uri}",
              "limit": 50, "access_token": tok}
    for _ in range(4):
        d = _get(url, params)
        reels += d.get("data", [])
        url, params = (d.get("paging") or {}).get("next"), {}
        if not url:
            break
    likes = {}
    try:  # likes et commentaires, si l'API les donne pour les reels
        d = _get(f"{GRAPH}/{pid}/video_reels", {"fields": "id,likes.summary(true).limit(0),comments.summary(true).limit(0)",
                                                 "limit": 50, "access_token": tok})
        likes = {x["id"]: ((x.get("likes") or {}).get("summary", {}).get("total_count"),
                           (x.get("comments") or {}).get("summary", {}).get("total_count")) for x in d.get("data", [])}
    except Exception:  # noqa: BLE001
        pass
    # vues des reels : le champ « views » de video_reels reste à 0 ; les vraies lectures sont dans video_insights
    # (fb_reels_total_plays / blue_reels_play_count, Lou 9 oct. : « pourquoi les quiz ont fait 0 vue »)
    plays, diag = {}, None
    for m in reels[:40]:
        try:
            d = _get(f"{GRAPH}/{m['id']}/video_insights", {"access_token": tok})
            vals = {x["name"]: (x.get("values") or [{}])[0].get("value") for x in d.get("data", [])}
            diag = diag or vals
            n = [vals.get(k) for k in ("fb_reels_total_plays", "blue_reels_play_count", "post_video_views")]
            n = [x for x in n if isinstance(x, (int, float))]
            if n:
                plays[m["id"]] = int(max(n))
        except Exception as ex:  # noqa: BLE001
            diag = diag or {"erreur": str(ex)[:300]}
    log(f"Facebook lectures (video_insights) : {len(plays)}/{min(len(reels), 40)} reels lus ; ex. {str(diag)[:400]}")
    for m in reels:
        at = int(datetime.strptime(m["created_time"], "%Y-%m-%dT%H:%M:%S%z").timestamp())
        v = db["videos"].setdefault(f"fb:{m['id']}", {"p": "facebook", "a": page["name"], "id": m["id"], "at": at})
        link = m.get("permalink_url") or ""
        thumb = ((m.get("thumbnails") or {}).get("data") or [{}])[0].get("uri") or m.get("picture")
        v.update(url=("https://www.facebook.com" + link) if link.startswith("/") else link, t=clean(m.get("description") or ""),
                 dur=m.get("length"), thumb_src=v.get("thumb_src") or thumb)
        lk, cm = likes.get(m["id"], (None, None))
        # sans read_insights, Facebook renvoie views = 0 même quand le reel est vu : « inconnu » plutôt que 0
        views = plays.get(m["id"]) if plays else (m.get("views") or None)
        snap(v, [views, lk, cm, None, None])
        v["r"] = robot["fb"].get(m["id"])


YT_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "m": "http://search.yahoo.com/mrss/"}


def yt_meta(vid: str, link: str) -> dict:
    """Date, durée, vues, likes d'une vidéo YouTube : sa page, sinon l'API « player » (11 oct. : depuis GitHub la page
    arrive sans date ni durée → « César » de Maths Expliqués jamais vue ; le player les donne même « UNPLAYABLE »)."""
    out: dict = {}
    try:
        page = requests.get(link, headers={**UA, "Accept-Language": "fr-FR"}, timeout=40).text
        m = re.search(r'"(?:publishDate|uploadDate)":"([^"]+)"', page)
        out["at"] = int(datetime.fromisoformat(m.group(1)).timestamp()) if m else None
        m = re.search(r'"lengthSeconds":"(\d+)"', page)
        out["dur"] = int(m.group(1)) if m else None
        m = re.search(r'"likeCount":"?(\d+)', page)
        out["likes"] = int(m.group(1)) if m else None
        m = re.search(r'"viewCount":"(\d+)"', page)
        out["views"] = int(m.group(1)) if m else None
    except Exception as ex:  # noqa: BLE001
        log(f"YouTube {vid} : page illisible ({ex})")
    if out.get("at") and out.get("dur"):
        return out
    try:
        r = requests.post("https://www.youtube.com/youtubei/v1/player?prettyPrint=false", headers=UA, timeout=40,
                          json={"videoId": vid, "context": {"client": {"clientName": "WEB", "clientVersion": "2.20251001.00.00",
                                                                       "hl": "fr", "gl": "FR"}}})
        j = r.json()
        vd = j.get("videoDetails") or {}
        mf = (j.get("microformat") or {}).get("playerMicroformatRenderer") or {}
        d = mf.get("publishDate") or mf.get("uploadDate")
        out["at"] = out.get("at") or (int(datetime.fromisoformat(d).timestamp()) if d else None)
        out["dur"] = out.get("dur") or (int(vd["lengthSeconds"]) if vd.get("lengthSeconds") else None)
        out["views"] = out.get("views") or (int(vd["viewCount"]) if vd.get("viewCount") else None)
        if not (out.get("at") and out.get("dur")):
            log(f"YouTube {vid} : player sans date/durée ({(j.get('playabilityStatus') or {}).get('status')})")
    except Exception as ex:  # noqa: BLE001
        log(f"YouTube {vid} : player illisible ({ex})")
    return out


def youtube(db: dict, handle: str, channel_id: str):
    """Les dernières vidéos de la chaîne (flux RSS : vues, likes) + durée et likes à jour sur la page de chaque
    vidéo récente. Depuis les serveurs de GitHub, YouTube refuse le téléchargement (« pas un robot ») mais pas ça.
    Le flux RSS répond 404 par moments (11 oct. 00:54 : AXIOM Officiel et Octave illisibles → leurs Shorts restaient
    « à poster » dans JARVIS) : la liste de la chaîne (yt-dlp, onglets Shorts + Vidéos) est TOUJOURS lue en plus, et
    une vidéo qu'elle seule voit prend sa date et sa durée sur sa page."""
    import xml.etree.ElementTree as ET
    import yt_dlp
    acc = db["accounts"].setdefault(f"youtube:{handle}", {"p": "youtube", "a": handle})
    seen: dict[str, dict] = {}  # id → {at, t, link, views, likes} (at None = à lire sur la page)
    try:
        r = requests.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", headers=UA, timeout=40)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        acc.update(name=root.findtext("a:author/a:name", namespaces=YT_NS) or handle)
        for e in root.findall("a:entry", YT_NS):
            vid = e.findtext("yt:videoId", namespaces=YT_NS)
            g = e.find("m:group", YT_NS)
            stats, star = g.find("m:community/m:statistics", YT_NS), g.find("m:community/m:starRating", YT_NS)
            link = (e.find("a:link", YT_NS).get("href") if e.find("a:link", YT_NS) is not None else None) or f"https://www.youtube.com/watch?v={vid}"
            seen[vid] = {"at": int(datetime.fromisoformat(e.findtext("a:published", namespaces=YT_NS)).timestamp()),
                         "t": g.findtext("m:title", namespaces=YT_NS) or "", "link": link,
                         "views": int(stats.get("views")) if stats is not None else None,
                         "likes": int(star.get("count")) if star is not None else None}
    except Exception as ex:  # noqa: BLE001
        log(f"YouTube @{handle} : flux RSS illisible ({ex}) — liste de la chaîne à la place")
    listed = False
    for tab in ("shorts", "videos"):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True,
                                    "playlistend": 15 if tab == "shorts" else 5}) as y:
                info = y.extract_info(f"https://www.youtube.com/@{handle}/{tab}", download=False)
        except Exception as ex:  # noqa: BLE001
            if tab == "shorts" or "does not have" not in str(ex):
                log(f"YouTube @{handle} {tab} : {ex}")
            continue
        listed = True
        if tab == "shorts":  # abonnés + photo de la chaîne
            acc["followers"] = info.get("channel_follower_count")
            acc.setdefault("fsnaps", {})[today()] = acc["followers"]
            av = [t for t in info.get("thumbnails") or [] if t.get("id") == "avatar_uncropped"]
            acc["avatar_src"] = acc.get("avatar_src") or (av[0]["url"] if av else None)
            acc["name"] = acc.get("name") or info.get("channel") or handle
        known = any(v["p"] == "youtube" and v["a"] == handle for v in db["videos"].values())  # chaîne déjà relevée
        # la liste va du plus récent au plus ancien : une vidéo inconnue est postée entre ses deux voisines connues
        # (avant la 1re connue = depuis le dernier passage) → date de secours si ni la page ni le player n'en donnent
        ents = [e for e in info.get("entries") or [] if e.get("id")]
        at_of = lambda vid: (seen.get(vid) or {}).get("at") or (db["videos"].get(f"yt:{vid}") or {}).get("at")
        for i, e in enumerate(ents):
            vid = e["id"]
            if vid in seen or f"yt:{vid}" in db["videos"]:
                continue
            older = next((at_of(x["id"]) for x in ents[i + 1:] if at_of(x["id"])), None)
            newer = next((at_of(x["id"]) for x in reversed(ents[:i]) if at_of(x["id"])), None)
            guess = None
            if known:
                guess = (older + 60) if older else None
                if not newer:  # plus récente que tout ce qu'on connaît : vue au passage qui suit sa mise en ligne
                    guess = int(time.time())
            seen[vid] = {"at": None, "t": e.get("title") or "", "link": f"https://www.youtube.com/watch?v={vid}",
                         "views": e.get("view_count"), "likes": None, "guess": guess}
    if not seen and not listed:
        raise RuntimeError("ni flux RSS ni liste de la chaîne lisibles")
    acc.update(url=f"https://www.youtube.com/@{handle}", found=True)
    for vid, s in seen.items():
        key = f"yt:{vid}"
        v = db["videos"].get(key)
        at = s["at"] or (v or {}).get("at")
        views, likes, meta = s["views"], s["likes"], {}
        # passage rapide (--meta) : la page seulement pour une vidéo nouvelle ; les stats, au relevé du matin
        if at is None or not (v or {}).get("dur") or (not META_ONLY and time.time() - at < FRESH_DAYS * 86400):
            meta = yt_meta(vid, s["link"])
        if at is None:
            if meta.get("at"):
                at = meta["at"]
            elif s.get("guess"):
                at = s["guess"]
                log(f"YouTube {vid} : ni page ni player ne donnent la date → estimée {datetime.fromtimestamp(at):%d/%m %H:%M}")
            else:
                log(f"YouTube {vid} : date de mise en ligne introuvable, vue au prochain passage")
                continue
        v = db["videos"].setdefault(key, {"p": "youtube", "a": handle, "id": vid, "at": at})
        if not v.get("thumb_src"):  # miniature verticale des Shorts ; les vidéos classiques n'ont que la 16:9
            oar = f"https://i.ytimg.com/vi/{vid}/oardefault.jpg"
            ok = requests.head(oar, timeout=20).status_code == 200
            v["thumb_src"] = oar if ok else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
            v["short"] = ok
        v.update(url=s["link"], t=clean(s["t"]))
        v["dur"] = meta.get("dur") or v.get("dur")
        likes = meta.get("likes") if meta.get("likes") is not None else likes
        views = meta.get("views") if meta.get("views") is not None else views
        snap(v, [views, likes, None, None, None])


def published_all(pf: str) -> list:
    """Publications du robot, historique compris : le registre Facebook d'avant le 10 oct. (posts supprimés par Lou,
    app Meta non publiée) est archivé à part pour que le robot reparte de zéro, mais JARVIS garde l'historique."""
    old = jload("published_fb_avant_10oct.json", []) if pf == "fb" else []
    return old + jload(f"published_{pf}.json", [])


def robot_marks() -> dict:
    """Ce que le robot (ou le PC avant lui) a publié : media_id / video_id → {k: repost|quiz|reel, src, key}."""
    reposts = jload("reposts.json", {}).get("videos", {})
    out = {"ig": {}, "fb": {}}
    for pf, idk in (("ig", "media_id"), ("fb", "video_id")):
        for r in published_all(pf):
            if not r.get(idk):
                continue
            src = reposts.get(r["key"][3:], {}).get("account") if r["key"].startswith("tt-") else None
            out[pf][r[idk]] = {"k": r.get("kind", "quiz"), "src": src, "by": r.get("par", "robot"), "key": r["key"]}
    return out


# ------------------------------------------------------------------ vignettes + envoi

def blob_put(files: list[tuple[Path, str, str, int]]) -> dict:
    """[(fichier, chemin dans le stockage, type, durée de cache s)] → {chemin: url publique}."""
    if not files:
        return {}
    man = WORK / "upload.json"
    man.write_text(json.dumps([[str(f), p, ct, age] for f, p, ct, age in files]), encoding="utf-8")
    out = subprocess.run(["node", str(Path(__file__).with_name("blob_put.mjs")), str(man)], capture_output=True,
                         text=True, timeout=600, cwd=ROOT)
    if out.returncode != 0:
        raise RuntimeError("envoi au stockage JARVIS impossible : " + (out.stderr or out.stdout)[-400:])
    return json.loads(out.stdout.strip().splitlines()[-1])


def thumbs(db: dict):
    """Vignette 9:16 (216x384, ~10 Ko) de chaque vidéo/compte qui n'en a pas encore, gardée dans le stockage."""
    todo = []
    items = [(f"{v['p']}-{v['id']}", v, "thumb_src", "th") for v in db["videos"].values()]
    items += [(f"avatar-{k.replace(':', '-')}", a, "avatar_src", "avatar") for k, a in db["accounts"].items()]
    for name, obj, src_key, out_key in items:
        if obj.get(out_key) or not obj.get(src_key):
            continue
        src, dst = WORK / f"{name}.src", WORK / f"{name}.jpg"
        try:
            r = requests.get(obj[src_key], headers=UA, timeout=30)
            r.raise_for_status()
            src.write_bytes(r.content)
            vf = ("scale=216:384:force_original_aspect_ratio=increase,crop=216:384" if out_key == "th"
                  else "scale=96:96:force_original_aspect_ratio=increase,crop=96:96")
            subprocess.run([fpr.FFMPEG, "-v", "error", "-y", "-i", str(src), "-vf", vf, "-q:v", "5", "-frames:v", "1",
                            str(dst)], check=True, timeout=60)
            todo.append((dst, f"jarvis/{'thumbs' if out_key == 'th' else 'avatars'}/{name}.jpg", "image/jpeg",
                         31536000 if out_key == "th" else 86400, obj, out_key))
        except Exception as ex:  # noqa: BLE001
            log(f"vignette {name} : {ex}")
    if not todo or LOCAL:
        return
    urls = blob_put([(f, p, ct, age) for f, p, ct, age, _, _ in todo])
    for f, p, ct, age, obj, key in todo:
        if p in urls:
            obj[key] = urls[p]
    log(f"{len(urls)} vignette(s) envoyée(s)")


QUEUE_N = 12  # 3 jours de créneaux


def queue(db: dict) -> dict:
    """Ce que le robot publiera aux prochains créneaux Instagram (Facebook suit), dans l'ordre de robot.publish_ig :
    reposts TikTok prêts d'abord, puis les quiz. JARVIS l'affiche dans son Planning (Lou, 9 oct. : « les posts prévus
    insta, même si c'est le robot qui les fait, juste marqué robot »). tracked/known/track_day = de quoi y prévoir les
    reposts des TikTok postés depuis le dernier relevé (relevé = 1er créneau servi de chaque jour)."""
    import onedrive as od
    import robot as rb
    out = []
    for rec in rb.repost_queue("ig")[:QUEUE_N]:
        v = db["videos"].get(f"tt:{rec['id']}", {})
        out.append({"k": "repost", "key": rec["key"], "src": rec.get("account"), "url": rec.get("url"),
                    "t": clean(rec.get("desc", ""), 90) or v.get("t", ""), "th": v.get("th"),
                    "dur": round(rec["duration"]) if rec.get("duration") else None})
    titles = db.setdefault("quiz_titles", {})
    for q in rb.quiz_queue("ig")[:max(0, QUEUE_N - len(out))]:
        if q["key"] not in titles:
            try:
                titles[q["key"]] = clean(od.read_text(q["txt"]).splitlines()[0], 90)
            except Exception as ex:  # noqa: BLE001
                log(f"légende du quiz {q['key']} : {ex}")
        out.append({"k": "quiz", "key": q["key"], "t": titles.get(q["key"]) or "Quiz " + q["key"].split("-", 2)[-1]})
    # spoufs : 3 / jour, aux créneaux SPOUF_SLOTS (12:30 · 17:30 · 20:30), pris dans « 00 » (db = le rapport en cours)
    sp = [{"k": "spouf", "key": x["key"], "t": x.get("title", ""), "th": x.get("th"),
           "dur": round(x["dur"]) if x.get("dur") else None} for x in rb.spouf_queue("ig", db)[:QUEUE_N]]
    rp = rb.db()
    return {"ig": out, "spouf": sp, "spouf_slots": list(rb.SPOUF_SLOTS), "track_day": rp.get("last_track_day"),
            "tracked": {h: a.get("since") for h, a in rp["accounts"].items()}, "known": sorted(rp["videos"]),
            # compte suivi en partie (« only ») : seuls ses fichiers OneDrive dont le chemin contient un de ces mots
            "only": {h: a["only"] for h, a in rp["accounts"].items() if a.get("only")}}


def daily_views(db: dict) -> dict:
    """Onglet Stats de JARVIS (Lou, 9 oct.) : vues gagnées chaque jour, par compte (« p:a »), toutes plateformes.
    Gain = vues au relevé du jour − vues au relevé précédent (le dernier relevé d'une journée fait foi, donc TikTok,
    relevé le matin, compte surtout la veille au soir). Jours sans relevé entre deux relevés (vidéos de plus de
    FRESH_DAYS jours, relevées le lundi) : gain réparti à parts égales sur ces jours, le reste sur le dernier.
    1er relevé d'une vidéo : ses vues comptent seulement si elle est sortie ce jour-là ou la veille (réparties depuis
    le jour de sortie) ; sinon c'est du passé non mesuré. Une baisse (correction de la plateforme) compte 0."""
    out: dict[str, dict[str, int]] = {}

    def add(d0: str, d1: str, n: int, key: str):  # n vues réparties sur les jours ]d0, d1]
        a = datetime.strptime(d0, "%Y-%m-%d")
        days = [(a + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(1, (datetime.strptime(d1, "%Y-%m-%d") - a).days + 1)]
        q, r = divmod(n, len(days))
        for i, d in enumerate(days):
            x = q + (r if i == len(days) - 1 else 0)
            if x:
                out.setdefault(d, {})[key] = out.get(d, {}).get(key, 0) + x

    for v in db["videos"].values():
        key = f"{v['p']}:{v['a']}"
        pts = [(d, s[0]) for d, s in sorted(v.get("snaps", {}).items()) if s and s[0] is not None]
        if not pts or not v.get("at"):
            continue
        pub = datetime.fromtimestamp(v["at"], TZ).date()
        d0, n0 = pts[0]
        if n0 > 0 and 0 <= (datetime.strptime(d0, "%Y-%m-%d").date() - pub).days <= 1:
            add((pub - timedelta(days=1)).isoformat(), d0, n0, key)
        for (da, na), (dz, nz) in zip(pts, pts[1:]):
            if nz > na:
                add(da, dz, nz - na, key)
    return dict(sorted(out.items()))


def build(db: dict) -> dict:
    """Le fichier lu par la page : compact, trié du plus récent au plus ancien."""
    yday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    order = jload("report_accounts.json", {}).get("order", [])
    accounts = []
    for k, a in db["accounts"].items():
        fs = a.get("fsnaps", {})
        accounts.append({"k": k, "p": a["p"], "a": a["a"], "name": a.get("name"), "url": a.get("url"),
                         "followers": a.get("followers"), "f1": (a.get("followers") or 0) - fs[yday]
                         if fs.get(yday) is not None and a.get("followers") is not None else None,
                         "count": a.get("count"), "avatar": a.get("avatar"), "found": a.get("found", True)})
    accounts.sort(key=lambda x: order.index(x["k"]) if x["k"] in order else 99)
    videos = []
    for v in db["videos"].values():
        sn = v.get("snaps", {})
        days = sorted(sn)
        cur = sn[days[-1]] if days else [None] * 5
        prev = sn.get((datetime.strptime(days[-1], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")) if days else None
        d1 = [c - p if c is not None and p is not None else None for c, p in zip(cur, prev)] if prev else None
        videos.append({"p": v["p"], "a": v["a"], "id": v["id"], "url": v.get("url"), "t": v.get("t", ""),
                       "at": v["at"], "dur": round(v["dur"]) if v.get("dur") else None, "th": v.get("th"),
                       "s": cur, "d1": d1, "day": days[-1] if days else None, "r": v.get("r")})
    videos.sort(key=lambda x: -x["at"])
    # Facebook garde la 1re image de la vidéo comme vignette (le quiz y est encore vide) : même publication du robot
    # sur Instagram → on reprend sa vignette.
    ig_th = {x["r"]["key"]: x["th"] for x in videos if x["p"] == "instagram" and x.get("r") and x["r"].get("key") and x["th"]}
    for x in videos:
        if x["p"] == "facebook" and x.get("r") and ig_th.get(x["r"].get("key")):
            x["th"] = ig_th[x["r"]["key"]]
    pubs = {pf: [{"at": r["at"], "k": r.get("kind"), "key": r["key"], "url": r.get("permalink"), "by": r.get("par", "robot")}
                 for r in published_all(pf) if r.get("kind") != "ancien reel" or pf == "ig"]
            for pf in ("ig", "fb")}
    try:
        plan = queue(db)
    except Exception as ex:  # noqa: BLE001 — sans OneDrive (--local) : pas de file
        log(f"file du robot : {ex}")
        plan = None
    return {"v": 1, "generated": datetime.now(TZ).isoformat(timespec="seconds"), "day": today(),
            "full": db.get("full_day"), "accounts": accounts, "videos": videos,
            "robot": {"slots": list(slots.IG), "fb_slots": list(slots.SLOTS), "since": "2026-10-08", "ig": pubs["ig"], "fb": pubs["fb"], "queue": plan},
            "perms": {"ig_views": bool(db.get("perms", {}).get("ig_insights"))},
            "daily_views": daily_views(db),
            "todo": db.get("todo", []), "todo_hold": db.get("todo_hold", []),
            "todo_done": [d for d in db.get("todo_done", []) if time.time() - d["moved"] < 7 * 86400]}


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    db = jload("report_db.json", {"accounts": {}, "videos": {}})
    db.setdefault("accounts", {})
    db.setdefault("videos", {})
    cfg = ensure_targets(jload("report_accounts.json", {"tiktok": []}))
    robot = robot_marks()
    for name, f in (("Instagram", lambda: instagram(db, robot)), ("Facebook", lambda: facebook(db, robot))):
        try:
            f()
        except Exception as ex:  # noqa: BLE001
            log(f"{name} : {ex}")
    for h in cfg.get("instagram", []):
        try:
            instagram_other(db, h)
        except Exception as ex:  # noqa: BLE001
            log(f"Instagram @{h} : {ex}")
    # TikTok + YouTube à chaque passage (4 / jour en plus du matin) : ce que Lou a posté à la main est reconnu
    # 30 min après son créneau, sans coche à la main (Lou, 9 oct.). Les stats détaillées restent au relevé du matin.
    for h in cfg.get("tiktok", []):
        try:
            tiktok_account(db, h)
        except Exception as ex:  # noqa: BLE001
            log(f"TikTok @{h} : {ex}")
    for h, cid in (cfg.get("youtube") or {}).items():
        try:
            youtube(db, h, cid)
        except Exception as ex:  # noqa: BLE001
            log(f"YouTube @{h} : {ex}")
    if not META_ONLY:
        tiktok_stats(db)
        db["full_day"] = today()
    try:  # les vidéos finies à poster (OneDrive) ; au relevé du matin, celles vues en ligne sont rangées
        todo.run(db, WORK, full=not META_ONLY, blob_put=(lambda f: {}) if LOCAL else blob_put, log=log, ig_media=IG_MEDIA)
    except Exception as ex:  # noqa: BLE001
        log(f"à poster : {ex}")
    thumbs(db)
    rep = build(db)
    jsave("report_db.json", db)  # après build : il y garde les titres des quiz en file
    out = WORK / "report.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"rapport : {len(rep['videos'])} vidéos, {len(rep['accounts'])} comptes, {out.stat().st_size // 1024} Ko")
    if not LOCAL:
        urls = blob_put([(out, "jarvis/report.json", "application/json", 60)])
        log(f"envoyé : {urls.get('jarvis/report.json')}")


if __name__ == "__main__":
    main()
