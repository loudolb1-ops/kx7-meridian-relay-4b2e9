"""Le robot Instagram d'AXIOM, dans le cloud (GitHub Actions) : il tourne même quand le PC de Lou est éteint.

Chaque créneau (08:00, 12:30, 17:30, 20:30, heure de Paris) :
  1. une fois par jour : relevé des comptes TikTok de Lou ; chaque nouvelle vidéo est retrouvée dans OneDrive
     « AXIOM acquisition » (empreinte image + son) — sinon, au relevé suivant, copie TikTok sans filigrane ;
  2. Instagram (Lou, 13 oct.), à chaque créneau : le plus ancien repost Maths Expliqués prêt (IG_FIRST), sinon un
     spouf de « 00 » (sans attendre TikTok), sinon un autre repost (Théo Rème), sinon un quiz ;
  3. Facebook (Lou, 10 oct. ~17:00 : « on recommence depuis le début. poste tout ce que tu veux sur facebook, des
     vidéos peu importe de quel compte, segmente au max, mais pas de quizz pour l'instant ») : 4 / jour, mêmes
     créneaux qu'Instagram ; depuis le 22 oct. (Lou : « oui quiz aussi sur Facebook ») 08:00 = un quiz « T'as le
     niveau ? » (vidéo de Page classique, meta.facebook_long : > 90 s), les 3 autres = les vidéos TikTok de Lou relevées par le rapport (tous ses comptes sauf
     @lou_maths, ≤ 90 s), comptes en alternance (le moins servi sur Facebook d'abord), les plus vues d'abord dans
     chaque compte (fb_mix_queue). Repart de zéro : les posts Facebook d'avant (app Meta non publiée, invisibles,
     0 lecture) restent en ligne, leur registre est archivé dans state/published_fb_avant_10oct.json.
  ⇒ DEPUIS LE 6 NOV. (Lou : « passe facebook à 8 par jour et des meilleurs qualités, tous ceux qui font des vues tous
     les jours, pas que du spoof, vraiment tout format confondu ») : Facebook = 8 / jour (slots.FB), sa propre file
     fb_top_queue (vues PAR JOUR sur la dernière semaine, tous les comptes, ≤ 90 s, original OneDrive si connu), publiée
     au créneau. Ce qui précède sur Facebook est l'historique.
  RÈGLE (Lou, 10 oct.) : jamais plus de 4 posts / jour sur Insta comme sur Facebook, un seul par créneau, et jamais deux
  fois la même vidéo sur un réseau — un spouf et son original TikTok sont la même vidéo (cid, spouf_sources.json).

python robot/robot.py slot     sert le créneau  [--force : même déjà servi]  [--dry : envoie à blanc, ne publie rien]
python robot/robot.py track    relevé TikTok seul
python robot/robot.py fbplan   programme Facebook 7 jours à l'avance (aussi après le rapport du matin)  [--dry]
python robot/robot.py status   état (aussi écrit dans OneDrive : « _robot Insta (ne pas toucher)/État.txt »)

L'état vit dans state/*.json, que le workflow committe après chaque passage. Les quiz sont fabriqués par le PC (quand
il est allumé) et déposés dans OneDrive « _robot Insta (ne pas toucher)/quiz ».
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import covers  # noqa: E402
import fingerprint as fpr  # noqa: E402
import meta  # noqa: E402
import onedrive as od  # noqa: E402
import slots  # noqa: E402
import tiktok  # noqa: E402
import renomme  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
WORK = ROOT / "work"
TZ = slots.TZ

ACQ = "AXIOM acquisition"
ROBOT_DIR = f"{ACQ}/_robot Insta (ne pas toucher)"
QUIZ_DIR = f"{ROBOT_DIR}/quiz"
COPIES_DIR = f"{ROBOT_DIR}/reposts"
COVERS_DIR = f"{ROBOT_DIR}/couvertures"  # JPEG des couvertures Insta (lien direct ~1 h)
SKIP_DIRS = {"AXIOM Insta quiz", "Gabriel Raconte"}  # + tout dossier qui commence par « _ » (archives, anciennes versions, ce robot) ; Gabriel Raconte retiré de JARVIS le 17 oct.

GRACE_H = 6
DUR_TOL = 1.5
MIN_IMAGE, MIN_SON = 0.75, 0.85  # calibré le 8 oct. : bon fichier 0.94 / 1.00, tous les autres ≤ 0.36 / ≤ 0.20
FB_MAX_S = 90
MAX_FAILS = 3
# Insta AXIOM (Lou, 9 oct. ~17:45 : « 3 spoofs/clips et 1 quiz ») : 12:30 · 17:30 · 20:30 = un spouf pris directement
# dans OneDrive « Spouf-Clipping…/00 » (vidéos finies, sans attendre TikTok) ; 08:00 = un quiz. Les reposts TikTok
# (Octave, Maths Expliqués, Théo Rème) ne passent plus que si la file du créneau est vide. Clé : sp-<id du fichier>.
SPOUF_SLOTS = ("12:30", "17:30", "20:30")
# Lou, 13 oct. : ces comptes passent en premier sur l'Insta AXIOM, à chaque créneau (order), et sont relevés à chaque
# créneau (cmd_slot) pour sortir le jour même : un TikTok de 12:00 part sur Insta à 12:30.
IG_FIRST = ("contre.calcul",)          # ex-@maths.expliques, renommé par Lou le 11 nov. 2026 (renomme.py)
# Lou, 14 oct. : les Maths Expliqués sortent maintenant sur le TikTok @chadsciences (suivi « only » dans reposts.json) →
# reconnus aussi par le dossier OneDrive de leur fichier (path / source du repost)
IG_FIRST_DIR = ("maths explique",)


def ig_first_rec(r: dict) -> bool:
    where = f"{r.get('path') or ''} {r.get('source') or ''}".lower()
    return r.get("account") in IG_FIRST or any(k in where for k in IG_FIRST_DIR)
FB_QUIZ_SLOT = "08:00"  # Facebook : au plus 1 quiz par jour, à ce créneau
SPOUF_RE = re.compile(r"spou?f|clip", re.I)
# Lou, 3 nov. ~03:45 : le compte AXIOM officiel poste autant partout (6 / jour) ; Insta, Facebook et YouTube = 2 Théo +
# 2 spoufs + 2 quiz (TikTok @axiomofficiel : 3 spoufs + 3 quiz), quiz partout. Maths Expliqués ne passe plus sur l'Insta
# AXIOM ; les Théo y passent au lieu d'Insta @chadsciences. Facebook = la vidéo qu'Insta vient de sortir au même créneau.
# Remplace l'ordre du 13 oct. (Maths Expliqués d'abord, puis spoufs / reposts, puis quiz) et SPOUF_SLOTS / FB_QUIZ_SLOT.
KIND_AT = {"08:00": "quiz", "12:30": "theo", "15:30": "quiz", "17:30": "spouf", "20:30": "theo", "22:30": "spouf"}
CATS = ("theo", "spouf", "quiz")
THEO_RE = re.compile(r"th[ée]o", re.I)


def is_theo(x: dict) -> bool:
    return bool(THEO_RE.search(x.get("folder") or ""))
HASHTAGS = "#maths #science #axiom"  # règle de Lou (10 oct.) : ces 3, jamais d'autres, tous comptes
CTA = "AXIOM 👉 lien en bio"  # jamais « l'appli… »
# Lou, 10 oct. : « 4 par jour » sur Insta, et jamais deux fois la même vidéo sur un même réseau. Plafond par jour de
# Paris (les anciens Reels reprogrammés sur Facebook ne comptent pas) + un seul post par créneau, même avec --force.
DAY_CAP = {"ig": len(slots.IG), "fb": len(slots.FB) + len(slots.FB_ONLY)}
# NN du spouf → id TikTok de la vidéo d'origine (écrit par le PC : shorts-montage/spoof-motion/tools/deliver_poster.py).
# Un spouf et son original sont la MÊME vidéo : l'objet le plus paradoxal est parti en spouf sur Facebook le 9 oct.
# et l'original attendait en tête de fb_best.json.
SOURCES = f"{ROBOT_DIR}/spouf_sources.json"

DRY = "--dry" in sys.argv


# ------------------------------------------------------------------ état

def jload(name: str, default):
    p = STATE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def jsave(name: str, data):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def now() -> datetime:
    return datetime.now(TZ)


def stamp() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str):
    line = f"{stamp()}  {msg}"
    print(line, flush=True)
    p = STATE / "journal.txt"
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    p.write_text("\n".join((lines + [line])[-800:]) + "\n", encoding="utf-8")


def db() -> dict:
    d = jload("reposts.json", {})
    d.setdefault("accounts", {})
    d.setdefault("videos", {})
    return d


# ------------------------------------------------------------------ OneDrive : fichiers candidats

_listing: list[dict] | None = None


def acq_files() -> list[dict]:
    """Les .mp4 de « AXIOM acquisition » avec leur durée (lue dans OneDrive, sinon mesurée une fois et gardée)."""
    global _listing
    if _listing is not None:
        return _listing
    durs = jload("durations.json", {})
    out = []
    skip = lambda n: n.startswith("_") or n in SKIP_DIRS  # noqa: E731
    for it in od.walk(ACQ, skip):
        if not it["name"].lower().endswith(".mp4"):
            continue
        d = (it.get("video") or {}).get("duration")
        if d:
            it["dur"] = d / 1000
        else:
            hit = durs.get(it["id"])
            if hit and hit[0] == it.get("cTag"):
                it["dur"] = hit[1]
            else:
                local = od.download(it["id"], WORK / "od" / f"{it['id']}.mp4")
                it["dur"] = fpr.duration(local)
                durs[it["id"]] = [it.get("cTag"), it["dur"]]
        out.append(it)
    jsave("durations.json", durs)
    _listing = out
    return out


_fps: dict[str, dict] = {}


def fp_item(item_id: str) -> dict:
    if item_id not in _fps:
        local = WORK / "od" / f"{item_id}.mp4"
        if not local.exists():
            od.download(item_id, local)
        _fps[item_id] = fpr.compute(local)
    return _fps[item_id]


def fp_tiktok(rec: dict) -> tuple[Path, dict]:
    small = WORK / "tt" / rec["id"]
    have = sorted(small.parent.glob(small.name + ".*")) if small.parent.exists() else []
    path = have[0] if have else tiktok.download(rec["url"], small, best=False)
    key = f"tt:{rec['id']}"
    if key not in _fps:
        _fps[key] = fpr.compute(path)
    return path, _fps[key]


def same(s: dict) -> bool:
    return s.get("image", 0) >= MIN_IMAGE and s.get("son", 0) >= MIN_SON


# ------------------------------------------------------------------ préparation d'un repost

def clean_title(name: str) -> str:
    t = re.sub(r"^\s*\d+\s*[-–.]\s*", "", Path(name).stem)
    return re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()


def caption(desc: str, fallback: str = "") -> str:
    """Une ligne, comme les posts TikTok de Lou : « 1+1≠2 🤯 #maths #science #axiom AXIOM 👉 lien en bio »."""
    text = re.split(r"\n\s*\n|Commentaire à épingler", desc or "")[0]
    text = re.sub(r"[#@]\S+", "", text)
    text = re.sub(r"(l'appli[^\n]*?)?\bAXIOM\W*(lien|link) (en|in) bio", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -–|·,") or fallback
    return f"{text} {HASHTAGS} {CTA}".strip()


def ig_ok(info: dict) -> bool:
    return (info["vcodec"] in ("h264", "hevc") and info["acodec"] == "aac" and info["sr"] <= 48000
            and max(info["w"] or 0, info["h"] or 0) <= 1920 and info["bitrate"] <= 24_000_000
            and info["size"] <= 290e6)


def check_video(info: dict):
    if not info["vcodec"]:
        raise ValueError("pas une vidéo (diaporama photo ?)")
    if info["duration"] < 3:
        raise ValueError("moins de 3 s")
    if info["duration"] > 900:
        raise ValueError("plus de 15 min (trop long pour un Reel)")


def transcode(src: Path, dst: Path) -> Path:
    vf = ("scale=w='min(1080,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease,"
          "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    subprocess.run([fpr.FFMPEG, "-v", "error", "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-preset", "fast",
                    "-crf", "20", "-maxrate", "20M", "-bufsize", "40M", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(dst)], check=True)
    return dst


def ready_from_file(rec: dict, local: Path, item: dict | None):
    """item = le fichier OneDrive d'origine (publié tel quel s'il convient à Instagram) ; sinon on envoie une copie
    conforme dans « _robot Insta (ne pas toucher)/reposts »."""
    info = fpr.probe(local)
    check_video(info)
    if item and ig_ok(info):
        rec.update(source="onedrive", item_id=item["id"], path=item["rel"])
    else:
        up = local if ig_ok(info) else transcode(local, local.with_name(f"tt-{rec['id']}.ig.mp4"))
        got = od.upload(up, f"{COPIES_DIR}/tt-{rec['id']}.mp4")
        rec.update(source="copie" + (" de " + item["rel"] if item else " TikTok"), item_id=got["id"],
                   path=f"{COPIES_DIR}/tt-{rec['id']}.mp4", **({"src_id": item["id"]} if item else {}))
        info = fpr.probe(up)
    title = clean_title(item["name"]) if item else ""
    rec.update(status="prête", duration=round(info["duration"], 2), caption=caption(rec.get("desc", ""), title),
               ready_at=stamp())
    log(f"repost prêt : @{rec['account']} {rec['id']} ({rec['duration']} s, {rec['source']}"
        + (f" {item['rel']}" if rec["source"] == "onedrive" else "") + ")")


def process(d: dict, rec: dict):
    small, a = fp_tiktok(rec)
    if a["frames"].shape[0] == 0:
        rec.update(status="ignorée", note="pas de vidéo (diaporama photo ?)")
        log(f"repost ignoré : @{rec['account']} {rec['id']} n'est pas une vidéo")
        return
    rec["tt_duration"] = round(a["duration"], 2)
    for other in d["videos"].values():  # la même vidéo postée sur deux comptes : une seule fois sur Instagram
        if other is rec or other.get("status") != "prête" or abs(other.get("tt_duration", -99) - a["duration"]) > DUR_TOL:
            continue
        if same(fpr.compare(a, fp_tiktok(other)[1])):
            rec.update(status="doublon", note=f"même vidéo que {other['id']} (@{other['account']})")
            log(f"repost ignoré : @{rec['account']} {rec['id']} = doublon de {other['id']}")
            return
    tries = []
    for it in acq_files():
        if abs(it["dur"] - a["duration"]) <= DUR_TOL:
            tries.append((it, fpr.compare(a, fp_item(it["id"]))))
    tries.sort(key=lambda t: t[1]["image"] + t[1]["son"], reverse=True)
    best, s = tries[0] if tries else (None, {})
    rec["score"] = s
    # compte suivi en partie seulement (« only » dans reposts.json) : @chadsciences → seuls ses spoufs partent sur
    # Instagram AXIOM ; ses originaux vont à la main sur Insta @chadsciences (Lou, 9 oct. 2026)
    only = (d["accounts"].get(rec["account"]) or {}).get("only")
    if only and not (best and same(s) and any(k in best["rel"].lower() for k in only)):
        if (best and same(s)) or time.time() - rec["seen"] > GRACE_H * 3600:
            rec.update(status="ignorée", note=f"pas un fichier « {' / '.join(only)} » (original : Insta à la main)")
            log(f"repost ignoré : @{rec['account']} {rec['id']} n'est pas un spouf"
                + (f" ({best['rel']})" if best and same(s) else ""))
        elif rec.get("status") != "attente fichier":
            rec["status"] = "attente fichier"
            log(f"repost en attente : @{rec['account']} {rec['id']} pas encore reconnu dans OneDrive")
        return
    if best and same(s):
        twin = next((v for v in d["videos"].values() if v is not rec and v.get("status") == "prête"
                     and v.get("source") == "onedrive" and v.get("item_id") == best["id"]), None)
        if twin:
            rec.update(status="doublon", note=f"même fichier que {twin['id']} (@{twin['account']})")
            log(f"repost ignoré : @{rec['account']} {rec['id']} = même fichier que {twin['id']}")
            return
        ready_from_file(rec, WORK / "od" / f"{best['id']}.mp4", best)
    elif best and s.get("image", 0) >= MIN_IMAGE:
        rec["note"] = f"image de {best['rel']} mais son différent → la version TikTok fait foi"
        ready_from_file(rec, tiktok.download(rec["url"], WORK / "best" / rec["id"], best=True), None)
    elif time.time() - rec["seen"] > GRACE_H * 3600:
        rec["note"] = f"aucun fichier OneDrive après {GRACE_H} h"
        ready_from_file(rec, tiktok.download(rec["url"], WORK / "best" / rec["id"], best=True), None)
    elif rec.get("status") != "attente fichier":
        rec["status"] = "attente fichier"
        log(f"repost en attente : @{rec['account']} {rec['id']} pas encore dans OneDrive (copie TikTok au prochain relevé sinon)")


def track():
    d = db()
    d["last_track_day"] = now().strftime("%Y-%m-%d")
    for handle, acc in d["accounts"].items():
        try:
            vids = tiktok.latest(handle)
        except Exception as ex:  # noqa: BLE001 — un compte en panne ne bloque pas les autres
            msg = str(ex)[:300]
            if acc.get("last_error") != msg:
                log(f"TikTok @{handle} relevé impossible : {msg}")
            acc.update(last_error=msg, last_check=stamp())
            continue
        acc["last_check"] = stamp()
        if vids is None:
            if acc.get("last_error") != "introuvable":
                log(f"TikTok @{handle} introuvable (compte pas encore créé ?) — re-cherché à chaque relevé")
            acc["last_error"] = "introuvable"
            continue
        acc.update(last_ok=stamp(), last_error=None)
        for v in vids:
            if v["id"] in d["videos"] or v["posted"] < acc["since"] or v["private"]:
                continue
            rec = d["videos"][v["id"]] = {
                "id": v["id"], "account": handle, "url": f"https://www.tiktok.com/@{handle}/video/{v['id']}",
                "posted": v["posted"], "posted_s": datetime.fromtimestamp(v["posted"], TZ).strftime("%Y-%m-%d %H:%M"),
                "desc": v["desc"], "seen": time.time(), "status": "nouvelle"}
            log(f"TikTok @{handle} nouvelle vidéo {v['id']} ({rec['posted_s']}) : « {v['desc'][:60]} »")
    jsave("reposts.json", d)
    for rec in sorted(d["videos"].values(), key=lambda r: r["posted"]):
        if rec["status"] not in ("nouvelle", "attente fichier"):
            continue
        try:
            process(d, rec)
            rec.pop("error", None)
        except Exception as ex:  # noqa: BLE001
            rec["error"] = str(ex)[:300]
            rec["tries"] = rec.get("tries", 0) + 1
            log(f"repost @{rec['account']} {rec['id']} : {rec['error']}")
            if isinstance(ex, ValueError) or rec["tries"] >= 4:
                rec.update(status="ignorée" if isinstance(ex, ValueError) else "échec", note=rec["error"])
        jsave("reposts.json", d)


# ------------------------------------------------------------------ files d'attente

def published(platform: str) -> list[dict]:
    return jload(f"published_{platform}.json", [])


def record(platform: str, rec: dict):
    """Une clé n'est publiée qu'une fois : une nouvelle inscription remplace l'ancienne (Facebook : « acceptée »
    puis « publiée »)."""
    lst = [r for r in published(platform) if r.get("key") != rec["key"]]
    jsave(f"published_{platform}.json", lst + [rec])


# ------------------------------------------------------------------ une vidéo = un post par réseau

_sources: dict | None = None


def spouf_sources() -> dict:
    global _sources
    if _sources is None:
        try:
            it = od.by_path(SOURCES)
            _sources = json.loads(od.read_text(it["id"])) if it else {}
        except Exception as ex:  # noqa: BLE001
            log(f"{SOURCES} illisible : {ex}")
            _sources = {}
    return _sources


def norm(s: str) -> str:
    """Titre comparable : sans #/@, sans accents ni ponctuation, en minuscules."""
    import unicodedata
    s = re.sub(r"[#@]\S+", " ", s or "")
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", s))


def cid(kind: str, x: dict) -> str:
    """La vidéo elle-même, quel que soit le chemin : un spouf = l'original TikTok dont il est tiré ; repost et meilleure
    vidéo = leur id TikTok ; quiz = son id."""
    if kind in ("repost", "best"):
        return f"tt:{x.get('orig') or x['id']}"  # orig : le TikTok @axiomofficiel d'un spouf = son original (fb_top_queue)
    if kind == "spouf":
        if x.get("tt"):
            return f"tt:{x['tt']}"
        nn = re.match(r"\s*(\d+)", x.get("name") or "") if SPOUF_RE.search(x.get("folder") or "spouf") else None
        src = nn and spouf_sources().get(nn.group(1).zfill(2))
        return f"tt:{src}" if src else f"sp:{x.get('item_id') or x.get('id')}"
    return x["key"]


def title_of(kind: str, x: dict) -> str:
    return {"repost": x.get("desc"), "best": x.get("t"),
            "spouf": x.get("title") or clean_title(x.get("name") or "")}.get(kind) or ""


def rec_cid(r: dict) -> str:
    if r.get("cid"):
        return r["cid"]
    k = r.get("key", "")
    return "tt:" + k.split("-", 1)[1] if k.startswith(("tt-", "best-")) else k


def fresh(platform: str, kind: str, items: list[dict]) -> list[dict]:
    """Retire de la file ce qui est déjà sorti sur ce réseau sous une autre clé (même id d'origine, ou même titre —
    filet si spouf_sources.json manque une ligne)."""
    recs = [r for r in published(platform) if r.get("kind") != "ancien reel"]
    cids = {rec_cid(r) for r in recs}
    titles = {t for t in (norm(r.get("title", "")) for r in recs) if len(t) >= 8}
    out = []
    for x in items:
        t = norm(title_of(kind, x)) if kind != "quiz" else ""
        if cid(kind, x) in cids or (len(t) >= 8 and any(t in o or o in t for o in titles)):
            continue
        out.append(x)
    return out


def served(platform: str, slot_key: str) -> str | None:
    """Pourquoi ce réseau ne doit plus rien publier maintenant (plafond du jour, créneau déjà servi), sinon None."""
    if DRY:
        return None  # un essai à blanc doit pouvoir tester la chaîne à tout moment
    day = now().strftime("%Y-%m-%d")
    today = [r for r in published(platform) if str(r.get("at", "")).startswith(day) and r.get("kind") != "ancien reel"]
    if len(today) >= DAY_CAP[platform]:
        return f"déjà {len(today)} post(s) aujourd'hui (max {DAY_CAP[platform]})"
    if slot_key and any(r.get("slot") == slot_key for r in today):
        return f"créneau {slot_key} déjà servi"
    return None


def failed(key: str, platform: str) -> int:
    f = jload("fails.json", {})
    n = f.setdefault(key, {}).get(platform, 0) + 1
    f[key][platform] = n
    jsave("fails.json", f)
    return n


def fails(key: str, platform: str) -> int:
    return jload("fails.json", {}).get(key, {}).get(platform, 0)


def hand_ig(path: str) -> bool:
    """Lou, 31 oct. (« 12 par jour sur insta chadsciences », 6 Théo + 6 voix de Lou) : le dossier de la vidéo part sur un
    Insta posté à la main (todo_accounts.json : ig_auto false, Théo Rème → @chadsciences) → plus sur l'Insta AXIOM
    (une vidéo = un post par réseau)."""
    accs = jload("todo_accounts.json", {})
    k = next((k for k in sorted(accs, key=len, reverse=True) if (path or "").startswith(k + "/")), None)
    a = accs.get(k) or {}
    return a.get("ig_auto") is False and a.get("instagram") not in (None, "axiomscience.app")


def repost_queue(platform: str) -> list[dict]:
    ig_done = {r["key"] for r in published("ig")}
    done = {r["key"] for r in published(platform)}
    out = []
    for rec in sorted(db()["videos"].values(), key=lambda r: r["posted"]):
        key = f"tt-{rec['id']}"
        if rec.get("status") != "prête" or key in done or fails(key, platform) >= MAX_FAILS:
            continue
        if platform == "ig" and hand_ig(rec.get("path")):
            continue
        if platform == "fb" and (key not in ig_done or rec.get("duration", 0) > FB_MAX_S):
            continue
        out.append({**rec, "key": key})
    return fresh(platform, "repost", out)


_quiz: list[dict] | None = None


def quiz_files() -> list[dict]:
    global _quiz
    if _quiz is None:
        folder = od.by_path(QUIZ_DIR)
        _quiz = od.children(folder["id"]) if folder else []
    return _quiz


def quiz_queue(platform: str) -> list[dict]:
    """Quiz déposés par le PC (AAAAMMJJ-NNNN-thème.mp4 + .txt + .json), du plus ancien au plus récent."""
    files = {it["name"]: it for it in quiz_files()}
    done = {r["key"] for r in published(platform)}
    out = []
    for name in sorted(files):
        m = re.match(r"^(\d{8}-\d{4}-[\w-]+)\.mp4$", name)
        if not m or m.group(1) in done or fails(m.group(1), platform) >= MAX_FAILS:
            continue
        key = m.group(1)
        if f"{key}.txt" not in files:
            continue
        out.append({"key": key, "item_id": files[name]["id"], "txt": files[f"{key}.txt"]["id"],
                    "json": (files.get(f"{key}.json") or {}).get("id"),
                    "cover": (files.get(f"{key}.jpg") or files.get(f"{key}.png") or {}).get("id")})
    return out


# Lou, 30 oct. : « file en stock + théos + spoufs en stock jamais sortis, tout ce que tu peux, l'objectif c'est de
# charbonner sur ce compte » → l'Insta prend aussi, direct dans « 00 », les spoufs du stock (même sans ig_auto) et les
# Théo déjà sortis sur TikTok que le repost n'a pas pu prendre (TikTok bloque les téléchargements depuis GitHub : 39–41,
# 43–45). Jamais avant TikTok une vidéo qui doit y sortir (champ « tiktok » du rapport sans post TikTok).
DIRECT_DIRS = ("Spouf-Clipping", "Théo Rème")


def spoufs(rdb: dict | None = None) -> list[dict]:
    """Les spoufs finis (« à poster » + « à rallonger ») du rapport JARVIS (state/report_db.json), dans l'ordre NN.
    + les vidéos sans TikTok (Lou, 14 oct. : TikTok @axiomofficiel arrêté → les Théo Rème de moins d'1 min ne sortent
    que sur Insta et YouTube) : pas de TikTok à reposter, l'Insta AXIOM les prend direct dans « 00 », comme un spouf.
    + DIRECT_DIRS (30 oct.) ; « tt » = l'id TikTok du repost de la même vidéo, pour ne jamais la poster deux fois."""
    rdb = rdb if rdb is not None else jload("report_db.json", {})
    by_file = {Path(r["path"]).name: r for r in db()["videos"].values() if r.get("path")}
    items = []
    for it in rdb.get("todo", []) + rdb.get("todo_hold", []):
        folder = it.get("folder") or ""
        if it.get("waiting"):
            continue
        if it.get("ig_auto") and (SPOUF_RE.search(folder) or not it.get("tiktok")):
            items.append(it)
            continue
        if not folder.startswith(DIRECT_DIRS):
            continue
        if it.get("ig_auto") is False and it.get("instagram") not in (None, "axiomscience.app"):
            continue  # Lou, 31 oct. : son Insta est posté à la main ailleurs (Théo, originaux → @chadsciences)
        if it.get("tiktok") and not (it.get("posts") or {}).get("tiktok"):
            continue  # TikTok d'abord
        rec = by_file.get(it.get("name") or "")
        if rec and rec.get("status") == "prête":
            continue  # le repost TikTok s'en charge
        items.append({**it, "tt": rec["id"] if rec else None})
    return sorted(items, key=lambda it: it.get("name") or "")


def spouf_queue(platform: str, rdb: dict | None = None) -> list[dict]:
    done = {r["key"] for r in published(platform)}
    ig_done = {r["key"] for r in published("ig")}
    out = []
    for it in spoufs(rdb):
        key = f"sp-{it['id']}"
        if key in done or fails(key, platform) >= MAX_FAILS:
            continue
        if platform == "ig" and (it.get("posts") or {}).get("instagram"):
            continue  # déjà sur Insta par un autre chemin
        if platform == "ig" and not it.get("mini"):
            continue  # Lou, 30 oct. : « avec la miniature à chaque fois » ; elle partira quand sa miniature sera là
        if platform == "fb" and key not in ig_done:
            continue  # Facebook suit Insta (> 90 s : vidéo de Page, publish_fb)
        out.append({**it, "key": key, "item_id": it["id"]})
    return fresh(platform, "spouf", out)


def spouf_caption(x: dict) -> str:
    """La légende du fichier (sans la consigne « Commentaire à épingler » destinée à Lou) + règle des hashtags."""
    return caption((x.get("caption") or "").split("Commentaire à épingler")[0], x.get("title", ""))


def spouf_url(x: dict) -> str:
    """Lien direct pour Instagram : le fichier de « 00 » s'il convient, sinon une copie conforme dans le dossier du robot."""
    local = od.download(x["item_id"], WORK / "sp" / f"{x['item_id'].replace('!', '_')}.mp4")
    info = fpr.probe(local)
    check_video(info)
    if ig_ok(info):
        return od.download_url(x["item_id"])
    got = od.upload(transcode(local, local.with_name(local.stem + ".ig.mp4")), f"{COPIES_DIR}/{x['key'].replace('!', '_')}.mp4")
    return od.download_url(got["id"])


def quiz_meta(q: dict) -> tuple[str, int | None]:
    cap = caption(od.read_text(q["txt"]))
    cover = json.loads(od.read_text(q["json"])).get("cover_ms") if q.get("json") else None
    return cap, cover


# ------------------------------------------------------------------ publication

def best_queue(platform: str = "fb") -> list[dict]:
    """Les meilleures vidéos TikTok de Lou (state/fb_best.json, vues décroissantes) pas encore publiées."""
    done = {r["key"] for r in published(platform)}
    out = []
    for v in jload("fb_best.json", {}).get("videos", []):
        key = f"best-{v['id']}"
        if key in done or fails(key, platform) >= MAX_FAILS or (v.get("dur") or 0) > FB_MAX_S:
            continue
        out.append({**v, "key": key})
    return fresh(platform, "best", out)


def quizzes_today(platform: str) -> int:
    day = now().strftime("%Y-%m-%d")
    return sum(1 for r in published(platform) if r.get("kind") == "quiz" and str(r.get("at", "")).startswith(day))


FB_SKIP_ACCOUNTS = {"lou_maths", "gabriel.raconte"}  # perso de Lou ; Gabriel = compte chrétien, jamais sur la Page AXIOM (14 oct.)


def fb_mix_queue() -> list[dict]:
    """Facebook : toutes les vidéos TikTok de Lou du rapport JARVIS (state/report_db.json), ≤ 90 s, pas encore sur
    Facebook ; un compte après l'autre (le moins servi sur Facebook d'abord), les plus vues d'abord dans chaque compte."""
    done = {r["key"] for r in published("fb")}
    per: dict[str, list[dict]] = {}
    for v in jload("report_db.json", {}).get("videos", {}).values():
        if v.get("p") != "tiktok" or v.get("a") in FB_SKIP_ACCOUNTS or not v.get("url") or v.get("gone"):
            continue
        if not v.get("dur") or v["dur"] > FB_MAX_S:
            continue
        key = f"best-{v['id']}"
        if key in done or fails(key, "fb") >= MAX_FAILS:
            continue
        views = max([s[0] or 0 for s in (v.get("snaps") or {}).values() if s] or [0])
        per.setdefault(v["a"], []).append({"id": v["id"], "a": v["a"], "account": v["a"], "url": v["url"],
                                           "dur": v["dur"], "t": v.get("t", ""), "views": views, "key": key})
    for lst in per.values():
        lst.sort(key=lambda x: -x["views"])
    count: dict[str, int] = {}
    for r in published("fb"):
        if r.get("account"):
            count[r["account"]] = count.get(r["account"], 0) + 1
    accounts = sorted(per, key=lambda a: (count.get(a, 0), -per[a][0]["views"]))
    out = []
    for i in range(max((len(l) for l in per.values()), default=0)):
        out += [per[a][i] for a in accounts if i < len(per[a])]
    return fresh("fb", "best", out)


# Lou, 6 nov. 2026 : « passe facebook à 8 par jour et des meilleurs qualités, tous ceux qui font des vues tous les jours,
# pas que du spoof, vraiment tout format confondu axiom + chadsciences + math explique » + « einstein kombat et einstein
# labo aussi ». Mesure du jour (report_db) : posts Facebook programmés d'avance = 2 vues en médiane (96 posts), publiés au
# créneau en Reel = 248 à 1 837 ; vidéos de Page longues (> 90 s, quiz) ≈ 2 → Reels ≤ 90 s seulement, publiés au créneau.
FB_TOP_FRESH_D = 10   # vues relevées il y a plus de 10 jours : on ne sait plus si elle « fait des vues tous les jours »
FB_TOP_RUN = 2        # au plus 2 vidéos d'affilée du même compte (« tout format confondu »)…
FB_TOP_ALT = (200, 0.05)  # …si l'autre compte fait au moins 200 vues / jour et 5 % de la meilleure (pas de remplissage)
FB_TOP_MIN = 20       # sous 20 vues / jour, elle ne « fait pas des vues tous les jours » : files d'Insta en secours
QUIZ_TITLE = re.compile(r"t.?as le niveau|tu fais \d+/\d+|\bquiz\b|test (de )?niveau", re.I)


_STOP = set("axiom lien en bio la le les l un une de du des d est cette ce cet a au aux qui que et ou ton ta tes ?? "
             "il elle on tu je pour sur dans par pas plus".split())


def tkey(t: str) -> set[str]:
    """Mots porteurs d'un titre (sans « AXIOM 👉 lien en bio » ni petits mots) : « Cette trompette est impossible à
    peindre » = « la trompette impossible à peindre »."""
    return {w for w in norm(t).split() if w not in _STOP and len(w) > 1}


def same_title(a: set[str], b: set[str]) -> bool:
    if len(a) < 2 or len(b) < 2:
        return False
    inter = len(a & b)
    return inter / len(a | b) >= 0.6 or (inter >= 3 and (a <= b or b <= a))


def views_per_day(v: dict) -> float:
    """Vues gagnées par jour sur la dernière semaine (relevés du rapport : snaps {jour: [vues, …]}) ; vidéo de moins
    d'une semaine : vues / âge. 0 si le dernier relevé date de plus de FB_TOP_FRESH_D jours."""
    from datetime import date
    sn = sorted((k, s[0]) for k, s in (v.get("snaps") or {}).items() if s and s[0] is not None)
    if not sn:
        return 0.0
    last_d, last = date.fromisoformat(sn[-1][0]), sn[-1][1]
    if (now().date() - last_d).days > FB_TOP_FRESH_D:
        return 0.0
    ref = next(((date.fromisoformat(k), n) for k, n in reversed(sn) if (last_d - date.fromisoformat(k)).days >= 6), None)
    if ref:
        return max(0, last - ref[1]) / (last_d - ref[0]).days
    age = max(1, (last_d - datetime.fromtimestamp(v.get("at") or 0, TZ).date()).days)
    return last / age


def tt_files() -> dict[str, dict]:
    """id TikTok → {f: fichier OneDrive (l'original, meilleure qualité que la copie TikTok), nn, folder} d'après le
    rapport (posts_map + vidéos rangées / à poster avec leurs posts TikTok)."""
    d = jload("report_db.json", {})
    out = {k[3:]: {"f": v["f"]} for k, v in (d.get("posts_map") or {}).items() if k.startswith("tt:") and v.get("f")}
    for it in (d.get("todo_done") or []) + (d.get("todo") or []):
        for p in ((it.get("posts") or {}).get("tiktok") or []):
            if p.get("id"):
                nn = re.match(r"\s*(\d+)", it.get("name") or it.get("title") or "")
                out[str(p["id"])] = {"f": it["id"], "nn": nn.group(1).zfill(2) if nn else None, "folder": it.get("folder") or ""}
    return out


def fb_top_queue() -> list[dict]:
    """Facebook (6 nov.) : les vidéos TikTok de TOUS les comptes de Lou (ChadSciences, AXIOM, Maths Expliqués, Einstein,
    Kombat… sauf FB_SKIP_ACCOUNTS) qui font le plus de vues PAR JOUR en ce moment, ≤ 90 s, jamais déjà sur Facebook
    (un spouf = son original), sans les quiz (vidéos de Page longues), au plus FB_TOP_RUN d'affilée du même compte."""
    done = {r["key"] for r in published("fb")}
    files, src = tt_files(), None
    best: dict[str, dict] = {}
    for v in jload("report_db.json", {}).get("videos", {}).values():
        if v.get("p") != "tiktok" or v.get("a") in FB_SKIP_ACCOUNTS or not v.get("url") or v.get("gone"):
            continue
        if not v.get("dur") or v["dur"] > FB_MAX_S or QUIZ_TITLE.search(v.get("t") or ""):
            continue
        key = f"best-{v['id']}"
        if key in done or fails(key, "fb") >= MAX_FAILS:
            continue
        fl = files.get(str(v["id"])) or {}
        orig = None
        if fl.get("nn") and SPOUF_RE.search(fl.get("folder", "")):
            src = src if src is not None else spouf_sources()
            orig = src.get(fl["nn"])
        x = {"id": v["id"], "a": v["a"], "account": v["a"], "url": v["url"], "dur": v["dur"], "t": v.get("t", ""),
             "key": key, "vpd": round(views_per_day(v), 1), "views": max([s[0] or 0 for s in (v.get("snaps") or {}).values() if s] or [0]),
             "file": fl.get("f"), "orig": str(orig) if orig else None}
        c = cid("best", x)
        if c not in best or x["vpd"] > best[c]["vpd"]:
            best[c] = x
    seen = [tkey(r.get("title", "")) for r in published("fb") if r.get("kind") != "ancien reel"]
    pool = []
    for x in sorted((x for x in fresh("fb", "best", list(best.values())) if x["vpd"] >= FB_TOP_MIN),
                    key=lambda x: (-x["vpd"], -x["views"])):
        k = tkey(x["t"])
        if any(same_title(k, o) for o in seen):  # même vidéo sous un autre TikTok (repost, autre compte) : une seule fois
            continue
        seen.append(k)
        pool.append(x)
    out: list[dict] = []
    while pool:
        pick = pool[0]
        run = [x["account"] for x in out[-FB_TOP_RUN:]]
        if len(run) == FB_TOP_RUN and len(set(run)) == 1 and pick["account"] == run[0]:
            alt = next((x for x in pool if x["account"] != run[0]), None)
            if alt and alt["vpd"] >= max(FB_TOP_ALT[0], FB_TOP_ALT[1] * pick["vpd"]):
                pick = alt
        pool.remove(pick)
        out.append(pick)
    return out


def ig_first(items: list[dict]) -> list[dict]:
    """Facebook suit Instagram : d'abord ce qu'Insta vient de publier (le spouf / quiz du même créneau, avant que la
    vidéo parte dans « 01 » et sorte de la file), puis le reste dans son ordre."""
    at = {r["key"]: str(r.get("at", "")) for r in published("ig")}
    return sorted(items, key=lambda x: at.get(x["key"], ""), reverse=True)


def cat_queues(platform: str) -> dict[str, list[dict]]:
    """Les 3 files du compte AXIOM (3 nov.) : Théo (dossier Théo Rème, après leur TikTok), spoufs, quiz. Facebook : ce
    qu'Insta vient de publier d'abord (même vidéo, même créneau)."""
    sq = spouf_queue(platform)
    q = {"theo": [x for x in sq if is_theo(x)], "spouf": [x for x in sq if not is_theo(x)], "quiz": quiz_queue(platform)}
    return {k: ig_first(v) for k, v in q.items()} if platform == "fb" else q


def order(platform: str, hm: str) -> list[tuple[str, list[dict]]]:
    """Ce que prend le créneau (3 nov.) : la file de son type (KIND_AT), puis les deux autres si elle est vide — jamais
    un créneau vide quand il reste de quoi poster. Le type de publication d'un Théo reste « spouf » (fichier de « 00 »)."""
    if platform == "fb":  # 6 nov. : Facebook a sa propre file (fb_top_queue) ; 3 essais par créneau, puis les files d'Insta
        top = fb_top_queue()
        q = cat_queues(platform)
        return [("best", [x]) for x in top[:3]] + [("spouf", q["spouf"]), ("spouf", q["theo"])]
    q = cat_queues(platform)
    want = KIND_AT.get(hm, "spouf")
    return [("quiz" if c == "quiz" else "spouf", q[c]) for c in (want, *[c for c in CATS if c != want])]


def best_file(x: dict) -> Path:
    """La vidéo de Lou en meilleure qualité : le fichier original de OneDrive quand le rapport le connaît (6 nov.), sinon
    la copie TikTok sans filigrane ; transcodée si Facebook risque de la refuser."""
    got = None
    if x.get("file"):
        try:
            got = WORK / "fb" / f"{x['key']}-src.mp4"
            od.download(x["file"], got)
            log(f"Facebook {x['key']} : fichier original OneDrive")
        except Exception as ex:  # noqa: BLE001 — fichier supprimé / déplacé hors d'atteinte : la copie TikTok
            log(f"Facebook {x['key']} : original OneDrive indisponible ({str(ex)[:120]}), copie TikTok")
            got = None
    got = got or tiktok.download(x["url"], WORK / "fb" / x["key"], best=True)
    info = fpr.probe(got)
    check_video(info)
    if info["vcodec"] == "h264" and info["acodec"] == "aac" and ig_ok(info):
        return got
    return transcode(got, WORK / "fb" / f"{x['key']}-fb.mp4")


def cover_for(kind: str, x: dict) -> Path | None:
    """La miniature de la vidéo en JPEG (covers.py, Lou 14 oct.) ; jamais bloquant : None = elle part sans."""
    try:
        rec = db()["videos"].get(str(x.get("id"))) if kind == "best" else None
        got = covers.find(kind, x, WORK / "covers", rec)
        log(f"couverture {x['key']} : {got[1] if got else 'aucune'}")
        return got[0] if got else None
    except Exception as ex:  # noqa: BLE001
        log(f"couverture {x['key']} impossible ({str(ex)[:160]})")
        return None


def cover_link(kind: str, x: dict) -> str | None:
    """Instagram va chercher la couverture lui-même : lien direct OneDrive du JPEG."""
    cov = cover_for(kind, x)
    if not cov:
        return None
    try:
        return covers.public_url(cov, x["key"], COVERS_DIR)
    except Exception as ex:  # noqa: BLE001
        log(f"couverture {x['key']} : envoi OneDrive impossible ({str(ex)[:160]})")
        return None


def publish_ig(hm: str = "", sk: str = "") -> bool | None:
    """True = publiée ; False = rien à publier ; None = bloqué exprès (plafond du jour, créneau déjà servi)."""
    why = served("ig", sk)
    if why:
        log(f"Instagram : rien publié, {why}")
        return None
    queues = order("ig", hm)
    planned = jload("ig_plan.json", {}).get("slots", {}).get(sk) if sk else None
    if planned:  # plan figé (30 oct.) : la vidéo prévue à ce créneau, si elle est toujours en file
        hit = next((x for k, q in queues if k == planned["kind"] for x in q if x["key"] == planned["key"]), None)
        if hit:
            queues = [(planned["kind"], [hit])] + queues
    for kind, q in queues:
        if not q:
            continue
        x = q[0]
        try:
            if kind == "repost":
                cap, cover, url = caption(x["caption"]), None, od.download_url(x["item_id"])
            elif kind == "spouf":
                cap, cover, url = spouf_caption(x), None, spouf_url(x)
            else:
                (cap, cover), url = quiz_meta(x), od.download_url(x["item_id"])
            link = cover_link(kind, x)
            res = meta.instagram(url, cap, None if link else cover, dry=DRY, cover_url=link)
        except meta.AccountDown as ex:   # le compte, pas la vidéo : aucun essai perdu, rien d'autre tenté
            log(f"Instagram COUPÉ : {ex}")
            return False
        except Exception as ex:  # noqa: BLE001
            n = failed(x["key"], "ig") if not DRY else 0
            log(f"ÉCHEC Instagram {kind} {x['key']} ({n}/{MAX_FAILS}) : {ex}")
            continue  # le quiz prend le créneau
        if DRY:
            log(f"(à blanc) Instagram accepterait {kind} {x['key']} : {res}")
            return True
        record("ig", {"key": x["key"], "kind": kind, "account": x.get("account"), "cid": cid(kind, x),
                      "title": title_of(kind, x)[:120], "slot": sk, **res})
        log(f"PUBLIÉE Instagram {kind} {x['key']}" + (f" (@{x['account']})" if x.get("account") else "")
            + f" → {res.get('permalink')}" + (f" [couverture {res['cover']}]" if res.get("cover") else ""))
        return True
    log("Instagram : rien à publier (ni repost ni quiz)")
    return False


def publish_fb(hm: str = "", sk: str = "") -> bool | None:
    why = served("fb", sk)
    if why:
        log(f"Facebook : rien publié, {why}")
        return None
    for kind, q in order("fb", hm):
        if not q:
            continue
        x = q[0]
        local = WORK / "fb" / f"{x['key'].replace('!', '_')}.mp4"
        try:
            if kind == "best":
                local = best_file(x)
            else:
                od.download(x["item_id"], local)
            cap = (caption(x["caption"]) if kind == "repost" else spouf_caption(x) if kind == "spouf"
                   else caption(x.get("t", "")) if kind == "best" else quiz_meta(x)[0])
            tag = {"key": x["key"], "kind": kind, "cid": cid(kind, x), "title": title_of(kind, x)[:120], "slot": sk,
                   "account": x.get("account")}
            long = kind == "quiz" or (x.get("dur") or 0) > FB_MAX_S
            send = meta.facebook_long if long else meta.facebook  # > 90 s (quiz, Théo / spouf longs) : pas un Reel
            res = send(local, cap, dry=DRY, cover=cover_for(kind, x), accepted=None if DRY else (
                lambda r, tag=tag: record("fb", {**tag, **r})))
        except meta.AccountDown as ex:   # jeton mort : aucun essai perdu
            log(f"Facebook COUPÉ : {ex}")
            return False
        except meta.RateLimited as ex:
            log(f"Facebook : {ex} — rien publié à ce créneau")
            return False
        except Exception as ex:  # noqa: BLE001
            n = failed(x["key"], "fb") if not DRY else 0
            log(f"ÉCHEC Facebook {kind} {x['key']} ({n}/{MAX_FAILS}) : {ex}")
            if any(r["key"] == x["key"] for r in published("fb")):
                return False  # acceptée puis en échec de traitement : on ne la remplace pas par un quiz
            continue
        if DRY:
            log(f"(à blanc) Facebook accepterait {kind} {x['key']} : {res}")
            return True
        record("fb", {**tag, **res})
        log(f"FACEBOOK {kind} {x['key']} → {res.get('permalink') or res['video_id']} ({res.get('state')})"
            + (f" [couverture {res['cover']}]" if res.get("cover") else ""))
        return True
    log("Facebook : rien à publier")
    return False


FB_PLAN_DAYS = 7  # Lou, 10 oct. ~17:10 : « planifie pour 1 semaine à l'avance »
FB_PLAN_MAX = 8   # par passage : au-delà de ~18 envois d'affilée, Facebook freine (10 oct.) ; le matin suivant complète


def cmd_fbplan():
    """Programme nativement sur la Page Facebook chaque créneau des 7 prochains jours (4 / jour, fb_mix_queue), visible
    dans Meta Business Suite → Planifié. Relancé chaque matin après le rapport : toujours 7 jours d'avance. Un créneau
    programmé compte comme servi (served) : le passage du créneau ne republie rien sur Facebook.
    ARRÊTÉ le 3 nov. : Facebook sort la même vidéo qu'Insta au même créneau (publish_fb au passage du créneau), et Lou ne
    veut plus rien de programmé à plus d'1 jour (shadowban) ; les vidéos déjà programmées restent (créneaux servis)."""
    log("Facebook : plus de programmation à l'avance (3 nov.) — publié au créneau, la même vidéo qu'Insta")
    return
    from datetime import timedelta
    t = now()
    taken = {r.get("slot") for r in published("fb")}
    todo = []
    for d in range(FB_PLAN_DAYS + 1):
        day = (t + timedelta(days=d)).date()
        for hm in slots.FB:
            hh, mm = map(int, hm.split(":"))
            st = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
            if t + timedelta(minutes=20) <= st <= t + timedelta(days=FB_PLAN_DAYS) and slots.key(st) not in taken:
                todo.append(st)
    log(f"Facebook : {len(todo)} créneau(x) à programmer d'ici {FB_PLAN_DAYS} jours")
    done = 0
    for st in todo[:FB_PLAN_MAX]:
        sk = slots.key(st)
        # 08:00 = le quiz du jour (Lou, 22 oct.), sinon / ensuite les vidéos TikTok de Lou
        quiz = [("quiz", q) for q in quiz_queue("fb")[:MAX_FAILS]] if sk.endswith(" " + FB_QUIZ_SLOT) else []
        for kind, x in quiz + [("best", b) for b in fb_mix_queue()[:MAX_FAILS]]:
            try:
                if kind == "quiz":
                    local = WORK / "fb" / f"{x['key']}.mp4"
                    od.download(x["item_id"], local)
                    cap, send = quiz_meta(x)[0], meta.facebook_long
                else:
                    local, cap, send = best_file(x), caption(x.get("t", "")), meta.facebook
                tag = {"key": x["key"], "kind": kind, "cid": cid(kind, x), "title": title_of(kind, x)[:120],
                       "slot": sk, "account": x.get("account"), "planned": True}
                res = send(local, cap, dry=DRY, when=st, cover=cover_for(kind, x),
                           accepted=None if DRY else (lambda r, tag=tag: record("fb", {**tag, **r})))
            except meta.AccountDown as ex:   # jeton mort : aucun essai perdu, on s'arrête au premier refus
                log(f"Facebook COUPÉ : {ex} — rien programmé ({done} programmée(s))")
                write_status()
                return
            except meta.RateLimited as ex:
                log(f"Facebook : {ex} — on s'arrête là, la suite au prochain passage ({done} programmée(s))")
                write_status()
                return
            except Exception as ex:  # noqa: BLE001
                n = failed(x["key"], "fb") if not DRY else 0
                log(f"ÉCHEC Facebook programmation {x['key']} ({n}/{MAX_FAILS}) : {ex}")
                continue
            if not DRY:
                record("fb", {**tag, **res})
            done += 1
            log(f"FACEBOOK programmée {sk} {kind} @{x.get('account')} {x['key']} : {x.get('t', '')[:60]}"
                + (f" [couverture {res['cover']}]" if res.get("cover") else ""))
            break
        else:
            log(f"Facebook : rien à programmer pour {sk}")
        if DRY:
            break  # à blanc : un seul essai suffit
    log(f"Facebook : {done} vidéo(s) programmée(s)")
    write_status()


def cmd_fbshift():
    """Pause du planning (slots.PAUSE) : chaque vidéo Facebook programmée à partir du 1er jour en pause, pas encore sortie,
    recule d'autant de jours que de jours en pause. Du plus tard au plus tôt, arrêt au 1er échec : au pire un créneau
    reste vide, jamais 2 vidéos sur un même créneau (axiome 1)."""
    from datetime import timedelta
    if not slots.PAUSE:
        log("Facebook : pas de pause, rien à décaler")
        return
    first, n, t = min(slots.PAUSE), len(slots.PAUSE), now()
    at = lambda sk: datetime.strptime(sk, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)  # noqa: E731
    recs = published("fb")
    todo = sorted((r for r in recs if r.get("planned") and r.get("video_id") and r.get("state") == "programmée"
                   and r.get("slot", "") >= first and r.get("pause") != first and at(r["slot"]) > t + timedelta(minutes=10)),
                  key=lambda r: r["slot"], reverse=True)
    log(f"Facebook : {len(todo)} vidéo(s) programmée(s) à décaler de {n} jour(s) (pause {', '.join(slots.PAUSE)})")
    done = 0
    for r in todo:
        new = at(r["slot"]) + timedelta(days=n)
        nk = slots.key(new)
        if any(o.get("slot") == nk and o.get("key") != r["key"] for o in published("fb")):
            log(f"Facebook : {nk} déjà pris, on s'arrête ({done} décalée(s))")
            break
        if DRY:
            log(f"(à blanc) {r['slot']} → {nk} {r['key']}")
            continue
        try:
            meta.fb_reschedule(r["video_id"], new)
        except Exception as ex:  # noqa: BLE001
            log(f"ÉCHEC Facebook décalage {r['slot']} {r['key']} : {ex} — on s'arrête ({done} décalée(s))")
            break
        record("fb", {**r, "slot": nk, "at": new.strftime("%Y-%m-%d %H:%M:%S"), "pause": first, "slot_avant": r["slot"]})
        done += 1
        log(f"FACEBOOK décalée {r['slot']} → {nk} @{r.get('account')} {r['key']}")
    log(f"Facebook : {done} vidéo(s) décalée(s)")
    write_status()


# ------------------------------------------------------------------ plan Insta figé (Lou, 30 oct.)

# Lou, 30 oct. : « planifie insta axiom pour 1 semaine à l'avance » → Instagram ne programme rien par l'API : on fige ici
# la vidéo de chaque créneau des 7 prochains jours (state/ig_plan.json + « Planning Insta.txt »), et le créneau la poste.
IG_PLAN_DAYS = 7
PLAN_TXT = f"{ROBOT_DIR}/Planning Insta.txt"
JOURS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


def ig_slots_ahead() -> list[datetime]:
    from datetime import timedelta
    t = now()
    out = []
    for d in range(IG_PLAN_DAYS + 1):
        day = (t + timedelta(days=d)).date()
        if day.strftime("%Y-%m-%d") in slots.PAUSE:
            continue
        for hm in slots.IG:
            hh, mm = map(int, hm.split(":"))
            st = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
            if t <= st <= t + timedelta(days=IG_PLAN_DAYS):
                out.append(st)
    return out


def cmd_igplan():
    """Une vidéo déjà prévue garde son créneau si elle est toujours en file et du type du créneau (KIND_AT, 3 nov.) ; les
    autres créneaux sont remplis dans leur file (Théo, spouf ou quiz), puis dans les deux autres si elle est vide."""
    future = ig_slots_ahead()
    keys = {slots.key(st) for st in future}
    old = {sk: o for sk, o in jload("ig_plan.json", {}).get("slots", {}).items() if sk in keys}
    queues = cat_queues("ig")
    avail = {x["key"]: (c, x) for c, lst in queues.items() for x in lst}
    want_of = lambda sk: KIND_AT.get(sk[-5:], "spouf")  # noqa: E731
    keep = {sk: o["key"] for sk, o in old.items() if o["key"] in avail and avail[o["key"]][0] == want_of(sk)}
    reserved = set(keep.values())
    used: set[str] = set()
    plan = {}
    for st in future:
        sk = slots.key(st)
        want = want_of(sk)
        if keep.get(sk) and keep[sk] not in used:
            pick = avail[keep[sk]]
        else:
            pick = next(((c, x) for c in (want, *[c for c in CATS if c != want]) for x in queues[c]
                         if x["key"] not in used and x["key"] not in reserved), None)
        if not pick:
            continue
        c, x = pick
        k = "quiz" if c == "quiz" else "spouf"
        used.add(x["key"])
        plan[sk] = {"kind": k, "cat": c, "key": x["key"], "title": (title_of(k, x) or x["key"])[:90]}
    jsave("ig_plan.json", {"made": stamp(), "slots": plan})
    lines = [f"Planning Insta AXIOM — {len(slots.IG)} posts / jour ({' · '.join(slots.IG)}), figé {IG_PLAN_DAYS} jours",
             f"Mis à jour le {now().strftime('%d/%m %H:%M')} : {len(plan)} créneaux prévus sur {len(future)}"
             + (f", {len(future) - len(plan)} vides (file vide)" if len(future) > len(plan) else ""), ""]
    day = None
    for st in future:
        if st.date() != day:
            day = st.date()
            lines += ["", f"{JOURS[st.weekday()]} {st.strftime('%d/%m')}"]
        p = plan.get(slots.key(st))
        lines.append(f"  {st.strftime('%H:%M')}  " + (f"{p.get('cat', p['kind']):<7} {p['title']}" if p else "—  (rien en file)"))
    try:
        od.write_text(PLAN_TXT, "\n".join(lines) + "\n")
    except Exception as ex:  # noqa: BLE001
        print(f"{PLAN_TXT} non écrit : {ex}")
    log(f"Planning Insta : {len(plan)} créneau(x) prévu(s) sur {len(future)} ({IG_PLAN_DAYS} jours)")
    return plan, future


# ------------------------------------------------------------------ état lisible (OneDrive)

def status_text() -> str:
    d = db()
    ig = published("ig")
    last = ig[-1] if ig else None
    rq, qq, sq = repost_queue("ig"), quiz_queue("ig"), spouf_queue("ig")
    waiting = sum(v["status"] in ("nouvelle", "attente fichier") for v in d["videos"].values())
    acc = " · ".join(f"@{h} " + ("introuvable" if a.get("last_error") == "introuvable" else
                                  "ERREUR" if a.get("last_error") else "ok") for h, a in d["accounts"].items())
    lines = [f"Robot Insta AXIOM — {now().strftime('%d/%m %H:%M')}",
             "",
             f"Dernier post : {last['at'][5:16]} {last['kind']} {last.get('permalink') or ''}" if last else "Dernier post : —",
             f"En attente : {len(sq)} spoufs · {len(rq)} TikTok · {len(qq)} quiz" + (f" · {waiting} sans fichier encore" if waiting else ""),
             f"Comptes : {acc}",
             "",
             f"Créneaux Insta ({' · '.join(slots.IG)}) : 2 Théo · 2 spoufs · 2 quiz par jour ("
             + " · ".join(f"{hm} {KIND_AT.get(hm, '?')}" for hm in slots.IG) + "), plan figé 7 jours (« Planning Insta.txt »)",
             f"Créneaux Facebook ({' · '.join(slots.FB)}) : {len(slots.FB)} / jour, les vidéos de tous les comptes qui font "
             "le plus de vues par jour (≤ 90 s, publiées au créneau)"]
    return "\n".join(lines) + "\n"


def write_status():
    try:
        od.write_text(f"{ROBOT_DIR}/État.txt", status_text())
    except Exception as ex:  # noqa: BLE001
        print(f"État.txt non écrit : {ex}")


# ------------------------------------------------------------------ commandes

def cmd_slot():
    s = slots.due()
    if not s and "--force" in sys.argv:
        s = slots.current() or now()
    if not s:
        print("aucun créneau à servir")
        return
    # relevé TikTok à chaque créneau (Lou, 13 oct. : les Maths Expliqués « pas en retard » sur Insta), avant : 1 / jour
    try:
        track()
    except Exception as ex:  # noqa: BLE001
        log(f"relevé TikTok en échec : {ex}")
    hm, sk = s.strftime("%H:%M"), slots.key(s)
    ig_slot = hm in slots.IG
    ok_ig = publish_ig(hm, sk) if ig_slot else None
    ok_fb = publish_fb(hm, sk) if hm in slots.FB or hm in slots.FB_ONLY else None
    if not DRY:
        jsave("slots.json", {"last_slot": slots.key(s), "at": stamp(), "ig": ok_ig, "fb": ok_fb})
    write_status()
    if ig_slot and ok_ig is False and (repost_queue("ig") or quiz_queue("ig") or spouf_queue("ig")):
        raise SystemExit(1)  # GitHub prévient Lou par e-mail : rien n'est parti alors qu'il y avait de quoi


def main():
    renomme.applique()   # avant toute lecture de l'état
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "slot":
        cmd_slot()
    elif cmd == "track":
        track()
        write_status()
    elif cmd == "fbplan":
        cmd_fbplan()
        if not DRY:
            cmd_igplan()
    elif cmd == "igplan":
        cmd_igplan()
        write_status()
    elif cmd == "fbshift":
        cmd_fbshift()
    else:
        print(status_text())


if __name__ == "__main__":
    main()
