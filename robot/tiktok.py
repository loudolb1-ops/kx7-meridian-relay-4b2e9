"""Comptes TikTok de Lou, lus sans connexion.

Liste des vidéos = la page « profil intégré » (tiktok.com/embed/@pseudo : les 10 dernières, avec la légende) — c'est la
seule qui répond depuis les serveurs de GitHub (l'API de liste de yt-dlp y renvoie du vide, testé le 9 oct. 2026).
La date de mise en ligne est dans l'identifiant de la vidéo (les 32 premiers bits = l'heure Unix).
Téléchargements = yt-dlp avec curl_cffi (sans l'imitation de navigateur, TikTok refuse les serveurs).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
HEAD = {"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"}


def posted_at(video_id: str) -> int:
    return int(video_id) >> 32


def latest(handle: str) -> list[dict] | None:
    """Les 10 dernières vidéos (None = compte introuvable : pas encore créé, ou pseudo changé)."""
    r = requests.get(f"https://www.tiktok.com/embed/@{handle}", headers=HEAD, timeout=40)
    m = re.search(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        if r.status_code in (400, 404):
            return None
        raise RuntimeError(f"page TikTok illisible (HTTP {r.status_code})")
    src = json.loads(m.group(1)).get("source", {}).get("data", {}).get(f"/embed/@{handle}", {})
    user = src.get("userInfo") or {}
    if str(user.get("code", "200")) != "200" or not user.get("id"):
        return None
    return [{"id": str(v["id"]), "desc": v.get("desc") or "", "posted": posted_at(str(v["id"])),
             "private": str(v.get("privateItem")).lower() == "true"} for v in src.get("videoList") or []]


def download(url: str, out_stem: Path, best: bool) -> Path:
    """best=False : la copie la plus légère (pour l'empreinte) ; best=True : la meilleure, sans filigrane
    (le format « download » est celui avec filigrane)."""
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    for old in out_stem.parent.glob(out_stem.name + ".*"):
        old.unlink()
    sort = "res,br" if best else "+size,+br"
    p = subprocess.run(["yt-dlp", "-q", "--no-warnings", "-S", sort, "-f", "b[format_id!=download]",
                        "-o", f"{out_stem}.%(ext)s", url], capture_output=True, text=True, timeout=600)
    got = [f for f in out_stem.parent.glob(out_stem.name + ".*") if not f.name.endswith(".part")]
    if p.returncode != 0 or not got:
        raise RuntimeError("téléchargement TikTok impossible : " + ((p.stderr or "").strip().splitlines() or ["?"])[-1][:200])
    return got[0]
