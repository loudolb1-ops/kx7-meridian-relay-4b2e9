"""Le OneDrive de Lou (loudolb1@gmail.com) par l'API Microsoft Graph.

Le jeton vient de rclone : rclone le rafraîchit tout seul et le réécrit dans son fichier de réglages (que le workflow
chiffre et garde dans le dépôt). Ici on ne fait que le lire, puis on appelle Graph directement.
"""
from __future__ import annotations

import configparser
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

GRAPH = "https://graph.microsoft.com/v1.0"
RCLONE = os.environ.get("RCLONE", "rclone")
CONF = os.environ.get("RCLONE_CONF", str(Path(__file__).resolve().parents[1] / "secret" / "rclone.conf"))
REMOTE = "od"
CHUNK = 320 * 1024 * 32  # 10 Mio, multiple de 320 Kio comme Graph l'exige

_tok: dict = {}


def token(force: bool = False) -> str:
    if force or not _tok or time.time() > _tok["until"]:
        # n'importe quelle commande rclone rafraîchit le jeton s'il a expiré (et l'enregistre dans CONF)
        subprocess.run([RCLONE, "--config", CONF, "about", f"{REMOTE}:"], check=True, capture_output=True, timeout=120)
        cp = configparser.ConfigParser()
        cp.read(CONF, encoding="utf-8")
        t = json.loads(cp[REMOTE]["token"])
        _tok.update(access=t["access_token"], until=time.time() + 30 * 60)
    return _tok["access"]


def call(method: str, url: str, **kw) -> requests.Response:
    url = url if url.startswith("http") else GRAPH + url
    for attempt in range(4):
        r = requests.request(method, url, headers={"Authorization": f"Bearer {token(attempt > 0)}"}, timeout=120, **kw)
        if r.status_code in (429, 503, 504):
            time.sleep(min(60, float(r.headers.get("Retry-After") or 5 * (attempt + 1))))
            continue
        if r.status_code == 401 and attempt == 0:
            continue
        return r
    return r


def get(url: str, **params) -> dict:
    r = call("GET", url, params=params or None)
    r.raise_for_status()
    return r.json()


def by_path(path: str) -> dict | None:
    r = call("GET", f"/me/drive/root:/{quote(path)}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def item(item_id: str) -> dict | None:
    r = call("GET", f"/me/drive/items/{item_id}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def children(item_id: str) -> list[dict]:
    out, url = [], f"/me/drive/items/{item_id}/children?$top=200"
    while url:
        d = get(url)
        out += d.get("value", [])
        url = d.get("@odata.nextLink")
    return out


def walk(root_path: str, skip=lambda name: False):
    """Tous les fichiers sous root_path, avec leur chemin relatif (« Spouf/01 - ….mp4 »)."""
    root = by_path(root_path)
    if not root:
        return
    stack = [(root["id"], "")]
    while stack:
        fid, rel = stack.pop()
        for it in children(fid):
            name = it["name"]
            if "folder" in it:
                if not skip(name):
                    stack.append((it["id"], f"{rel}{name}/"))
            elif "file" in it:
                it["rel"] = rel + name
                yield it


def download(item_id: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = item(item_id)["@microsoft.graph.downloadUrl"]
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    return dest


def download_url(item_id: str) -> str:
    """Lien direct pré-authentifié (valable ~1 h) : Instagram télécharge la vidéo depuis ce lien."""
    return item(item_id)["@microsoft.graph.downloadUrl"]


def upload(local: Path, path: str) -> dict:
    """Envoie un fichier (remplace s'il existe) ; renvoie l'élément OneDrive."""
    r = call("POST", f"/me/drive/root:/{quote(path)}:/createUploadSession",
             json={"item": {"@microsoft.graph.conflictBehavior": "replace"}})
    r.raise_for_status()
    up = r.json()["uploadUrl"]
    size = local.stat().st_size
    with open(local, "rb") as fh:
        pos = 0
        while pos < size:
            data = fh.read(CHUNK)
            end = pos + len(data) - 1
            for attempt in range(5):
                rr = requests.put(up, data=data, timeout=300,
                                  headers={"Content-Length": str(len(data)), "Content-Range": f"bytes {pos}-{end}/{size}"})
                if rr.status_code < 500:
                    break
                time.sleep(5 * (attempt + 1))
            rr.raise_for_status()
            pos = end + 1
    return rr.json()


def write_text(path: str, text: str) -> None:
    r = call("PUT", f"/me/drive/root:/{quote(path)}:/content", data=text.encode("utf-8"))
    r.raise_for_status()


def read_text(item_id: str) -> str:
    r = call("GET", f"/me/drive/items/{item_id}/content")
    r.raise_for_status()
    return r.content.decode("utf-8")
