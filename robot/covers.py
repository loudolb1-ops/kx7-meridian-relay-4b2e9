"""Couverture des reels que le robot poste lui-même (Lou, 14 oct. 2026 : « mets la couverture (miniature) sur les reels
que le robot poste lui-même (Insta + FB) »).

Où la trouver, dans l'ordre :
  1. quiz : « <id>.jpg » déposé par le PC avec le quiz ;
  2. la miniature livrée à côté du fichier OneDrive de la vidéo (« NN - Titre - miniature.png »), où qu'il soit rangé
     maintenant (00, 01, sous-dossier : on part de l'id du fichier, qui ne change pas quand il bouge) ;
  3. vidéo venue de TikTok sans miniature OneDrive : la couverture TikTok (= la miniature que Lou y a importée).
Jamais bloquant : sans couverture, la vidéo part comme avant (vignette choisie par Instagram / Facebook).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import requests

import fingerprint as fpr
import onedrive as od

ACQ = "AXIOM acquisition"
EXT = ("png", "jpg", "jpeg", "webp")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")


def sibling(item_id: str) -> dict | None:
    """La miniature posée à côté d'un fichier OneDrive (« <nom sans extension> - miniature.png »)."""
    it = od.item(item_id)
    if not it or not (it.get("parentReference") or {}).get("id"):
        return None
    stem = it["name"].rsplit(".", 1)[0]
    want = {f"{stem} - miniature.{e}".lower() for e in EXT}
    return next((k for k in od.children(it["parentReference"]["id"]) if k["name"].lower() in want), None)


def to_jpeg(src: Path, dst: Path) -> Path:
    """JPEG 1080×1920 (Instagram ne prend que du JPEG pour une couverture)."""
    subprocess.run([fpr.FFMPEG, "-v", "error", "-y", "-i", str(src), "-vf",
                    "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuvj420p",
                    "-q:v", "2", "-frames:v", "1", str(dst)], check=True, timeout=60)
    return dst


def tiktok_cover(url: str, work: Path, stem: str) -> Path | None:
    """La couverture d'une vidéo TikTok, de la meilleure à la moins bonne (les couvertures animées ne passent pas
    ffmpeg : on prend la suivante)."""
    p = subprocess.run(["yt-dlp", "-q", "--no-warnings", "-J", url], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return None
    info = json.loads(p.stdout)
    urls = [t["url"] for t in reversed(info.get("thumbnails") or []) if t.get("url")]
    for i, u in enumerate(dict.fromkeys(urls + [info.get("thumbnail") or ""])):
        if not u:
            continue
        raw = work / f"{stem}.tt{i}"
        try:
            r = requests.get(u, headers={"User-Agent": UA, "Accept": "image/jpeg,image/webp,image/*"}, timeout=60)
            r.raise_for_status()
            raw.write_bytes(r.content)
            return to_jpeg(raw, work / f"{stem}.jpg")
        except Exception:  # noqa: BLE001
            continue
    return None


def find(kind: str, x: dict, work: Path, rec: dict | None = None) -> tuple[Path, str] | None:
    """(JPEG local, d'où elle vient) pour l'élément x de la file « kind » ; rec = le repost TikTok de la même vidéo
    s'il existe (il connaît le fichier OneDrive d'origine). None = pas de couverture."""
    work.mkdir(parents=True, exist_ok=True)
    stem = x["key"].replace("!", "_")
    if kind == "quiz":
        if not x.get("cover"):
            return None
        return to_jpeg(od.download(x["cover"], work / f"{stem}.src"), work / f"{stem}.jpg"), "quiz"
    r = rec or x
    ids = [x.get("item_id")] if kind == "spouf" else []
    ids += [r.get("src_id"), r.get("item_id") if r.get("source") == "onedrive" else None]
    src = r.get("source") or ""
    if src.startswith("copie de "):   # copie conforme dans « _robot… » : la miniature est à côté de l'original
        orig = od.by_path(f"{ACQ}/{src[len('copie de '):]}")
        ids.append(orig and orig["id"])
    for i in dict.fromkeys(filter(None, ids)):
        s = sibling(i)
        if s:
            return to_jpeg(od.download(s["id"], work / f"{stem}.src"), work / f"{stem}.jpg"), s["name"]
    url = r.get("url") or x.get("url")
    if kind in ("repost", "best") and url:
        got = tiktok_cover(url, work, stem)
        if got:
            return got, "couverture TikTok"
    return None


def public_url(local: Path, key: str, folder: str) -> str:
    """Lien direct (~1 h) pour Instagram, qui va chercher la couverture lui-même."""
    got = od.upload(local, f"{folder}/{key.replace('!', '_')}.jpg")
    return od.download_url(got["id"])
