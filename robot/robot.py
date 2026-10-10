"""Le robot Instagram d'AXIOM, dans le cloud (GitHub Actions) : il tourne même quand le PC de Lou est éteint.

Chaque créneau (08:00, 12:30, 17:30, 20:30, heure de Paris) :
  1. une fois par jour : relevé des comptes TikTok de Lou ; chaque nouvelle vidéo est retrouvée dans OneDrive
     « AXIOM acquisition » (empreinte image + son) — sinon, au relevé suivant, copie TikTok sans filigrane ;
  2. Instagram : à 12:30, 17:30 et 20:30 un spouf de « 00 » (3 / jour, sans attendre TikTok), à 08:00 un quiz ;
     si la file est vide : le plus ancien repost TikTok prêt, puis l'autre file ;
  3. Facebook (Lou, 10 oct. ~16:30 : « garde le même quota que sur axiom insta : 3 spoofs par jour, 1 quizz par jour ») :
     4 / jour, mêmes créneaux qu'Instagram = 08:00 le quiz du jour (1 max), 12:30 · 17:30 · 20:30 un spouf déjà sur
     Instagram (≤ 90 s, du plus ancien au plus récent), repost TikTok déjà sur Instagram si plus de spouf, jamais de
     quiz en plus. Fini les « meilleures vidéos TikTok » (fb_best.json) et le créneau 15:00. Les posts Facebook d'avant
     (app Meta encore non publiée : invisibles, 0 lecture) ont été supprimés par Lou ; leur registre est archivé dans
     state/published_fb_avant_10oct.json, le robot repart de zéro le 10 oct. à 17:30.
  RÈGLE (Lou, 10 oct.) : jamais plus de 4 posts / jour sur Insta comme sur Facebook, un seul par créneau, et jamais deux
  fois la même vidéo sur un réseau — un spouf et son original TikTok sont la même vidéo (cid, spouf_sources.json).

python robot/robot.py slot     sert le créneau  [--force : même déjà servi]  [--dry : envoie à blanc, ne publie rien]
python robot/robot.py track    relevé TikTok seul
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
import fingerprint as fpr  # noqa: E402
import meta  # noqa: E402
import onedrive as od  # noqa: E402
import slots  # noqa: E402
import tiktok  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
WORK = ROOT / "work"
TZ = slots.TZ

ACQ = "AXIOM acquisition"
ROBOT_DIR = f"{ACQ}/_robot Insta (ne pas toucher)"
QUIZ_DIR = f"{ROBOT_DIR}/quiz"
COPIES_DIR = f"{ROBOT_DIR}/reposts"
SKIP_DIRS = {"AXIOM Insta quiz"}  # + tout dossier qui commence par « _ » (archives, anciennes versions, ce robot)

GRACE_H = 6
DUR_TOL = 1.5
MIN_IMAGE, MIN_SON = 0.75, 0.85  # calibré le 8 oct. : bon fichier 0.94 / 1.00, tous les autres ≤ 0.36 / ≤ 0.20
FB_MAX_S = 90
MAX_FAILS = 3
# Insta AXIOM (Lou, 9 oct. ~17:45 : « 3 spoofs/clips et 1 quiz ») : 12:30 · 17:30 · 20:30 = un spouf pris directement
# dans OneDrive « Spouf-Clipping…/00 » (vidéos finies, sans attendre TikTok) ; 08:00 = un quiz. Les reposts TikTok
# (Octave, Maths Expliqués, Théo Rème) ne passent plus que si la file du créneau est vide. Clé : sp-<id du fichier>.
SPOUF_SLOTS = ("12:30", "17:30", "20:30")
FB_QUIZ_SLOT = "08:00"  # Facebook : au plus 1 quiz par jour, à ce créneau
SPOUF_RE = re.compile(r"spou?f|clip", re.I)
HASHTAGS = "#maths #science #axiomscienceapp #axiom #studyapp #studytips"  # règle de Lou : ces 6, jamais d'autres
# Lou, 10 oct. : « 4 par jour » sur Insta, et jamais deux fois la même vidéo sur un même réseau. Plafond par jour de
# Paris (les anciens Reels reprogrammés sur Facebook ne comptent pas) + un seul post par créneau, même avec --force.
DAY_CAP = {"ig": len(slots.IG), "fb": len(slots.IG) + len(slots.FB_ONLY)}
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
    """Le texte de la vidéo TikTok + « AXIOM lien en bio » + les 6 hashtags fixes de Lou."""
    text = re.sub(r"[#@]\S+", "", desc or "")
    text = re.sub(r"\s+", " ", text).strip(" -–|·")
    text = text or fallback
    if not re.search(r"\bbio\b", text, re.I):
        text = f"{text} AXIOM lien en bio".strip()
    return f"{text}\n\n{HASHTAGS}"


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
                   path=f"{COPIES_DIR}/tt-{rec['id']}.mp4")
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
        return f"tt:{x['id']}"
    if kind == "spouf":
        nn = re.match(r"\s*(\d+)", x.get("name") or "")
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


def repost_queue(platform: str) -> list[dict]:
    ig_done = {r["key"] for r in published("ig")}
    done = {r["key"] for r in published(platform)}
    out = []
    for rec in sorted(db()["videos"].values(), key=lambda r: r["posted"]):
        key = f"tt-{rec['id']}"
        if rec.get("status") != "prête" or key in done or fails(key, platform) >= MAX_FAILS:
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
                    "json": (files.get(f"{key}.json") or {}).get("id")})
    return out


def spoufs(rdb: dict | None = None) -> list[dict]:
    """Les spoufs finis (« à poster » + « à rallonger ») du rapport JARVIS (state/report_db.json), dans l'ordre NN."""
    rdb = rdb if rdb is not None else jload("report_db.json", {})
    items = [it for it in rdb.get("todo", []) + rdb.get("todo_hold", [])
             if it.get("ig_auto") and not it.get("waiting") and SPOUF_RE.search(it.get("folder") or "")]
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
        if platform == "fb" and (key not in ig_done or (it.get("dur") or 0) > FB_MAX_S):
            continue
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
    cap = od.read_text(q["txt"])
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


def ig_first(items: list[dict]) -> list[dict]:
    """Facebook suit Instagram : d'abord ce qu'Insta vient de publier (le spouf / quiz du même créneau, avant que la
    vidéo parte dans « 01 » et sorte de la file), puis le reste dans son ordre."""
    at = {r["key"]: str(r.get("at", "")) for r in published("ig")}
    return sorted(items, key=lambda x: at.get(x["key"], ""), reverse=True)


def order(platform: str, hm: str) -> list[tuple[str, list[dict]]]:
    """Ce que prend le créneau. Instagram : 12:30 / 17:30 / 20:30 = un spouf d'abord ; 08:00 = un quiz d'abord ;
    repost en secours. Facebook (même quota qu'Insta) : 08:00 = le quiz du jour (1 max) ; 12:30 / 17:30 / 20:30 = un
    spouf, repost en secours, jamais un quiz de plus."""
    if platform == "fb":
        if hm == FB_QUIZ_SLOT:
            return [("quiz", ig_first(quiz_queue("fb")))] if not quizzes_today("fb") else []
        return [("spouf", ig_first(spouf_queue("fb"))), ("repost", ig_first(repost_queue("fb")))]
    sp, rp = ("spouf", spouf_queue(platform)), ("repost", repost_queue(platform))
    qz = ("quiz", quiz_queue(platform))
    return [sp, rp, qz] if hm in SPOUF_SLOTS else [qz, rp, sp]


def best_file(x: dict) -> Path:
    """La vidéo TikTok de Lou, meilleure qualité sans filigrane ; transcodée si Facebook risque de la refuser."""
    got = tiktok.download(x["url"], WORK / "fb" / x["key"], best=True)
    info = fpr.probe(got)
    check_video(info)
    if info["vcodec"] == "h264" and info["acodec"] == "aac" and ig_ok(info):
        return got
    return transcode(got, WORK / "fb" / f"{x['key']}-fb.mp4")


def publish_ig(hm: str = "", sk: str = "") -> bool | None:
    """True = publiée ; False = rien à publier ; None = bloqué exprès (plafond du jour, créneau déjà servi)."""
    why = served("ig", sk)
    if why:
        log(f"Instagram : rien publié, {why}")
        return None
    for kind, q in order("ig", hm):
        if not q:
            continue
        x = q[0]
        try:
            if kind == "repost":
                cap, cover, url = x["caption"], None, od.download_url(x["item_id"])
            elif kind == "spouf":
                cap, cover, url = spouf_caption(x), None, spouf_url(x)
            else:
                (cap, cover), url = quiz_meta(x), od.download_url(x["item_id"])
            res = meta.instagram(url, cap, cover, dry=DRY)
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
            + f" → {res.get('permalink')}")
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
            cap = (x["caption"] if kind == "repost" else spouf_caption(x) if kind == "spouf"
                   else caption(x.get("t", "")) if kind == "best" else quiz_meta(x)[0])
            tag = {"key": x["key"], "kind": kind, "cid": cid(kind, x), "title": title_of(kind, x)[:120], "slot": sk}
            res = meta.facebook(local, cap, dry=DRY, accepted=None if DRY else (
                lambda r, tag=tag: record("fb", {**tag, **r})))
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
        log(f"FACEBOOK {kind} {x['key']} → {res.get('permalink') or res['video_id']} ({res.get('state')})")
        return True
    log("Facebook : rien à publier")
    return False


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
             f"Facebook : {len(spouf_queue('fb'))} spoufs en file",
             "Créneaux Insta : 08:00 quiz · 12:30, 17:30, 20:30 spouf (repost TikTok si la file est vide)",
             "Créneaux Facebook : les mêmes (08:00 quiz · 12:30, 17:30, 20:30 spouf déjà sur Insta)"]
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
    if db().get("last_track_day") != now().strftime("%Y-%m-%d"):
        try:
            track()
        except Exception as ex:  # noqa: BLE001
            log(f"relevé TikTok en échec : {ex}")
    hm, sk = s.strftime("%H:%M"), slots.key(s)
    ig_slot = hm not in slots.FB_ONLY
    ok_ig = publish_ig(hm, sk) if ig_slot else None  # 15:00 : Facebook seul
    ok_fb = publish_fb(hm, sk)
    if not DRY:
        jsave("slots.json", {"last_slot": slots.key(s), "at": stamp(), "ig": ok_ig, "fb": ok_fb})
    write_status()
    if ig_slot and ok_ig is False and (repost_queue("ig") or quiz_queue("ig") or spouf_queue("ig")):
        raise SystemExit(1)  # GitHub prévient Lou par e-mail : rien n'est parti alors qu'il y avait de quoi


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "slot":
        cmd_slot()
    elif cmd == "track":
        track()
        write_status()
    else:
        print(status_text())


if __name__ == "__main__":
    main()
