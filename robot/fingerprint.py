"""Empreinte d'une vidéo pour retrouver, dans OneDrive, le fichier d'origine d'une vidéo postée sur TikTok.

TikTok ré-encode (540p–1080p, HEVC) mais garde l'image et le son : on compare
  - l'image : 4 vignettes 16x16 en gris par seconde (centrées, contraste normalisé) ;
  - le son : l'enveloppe d'énergie (log RMS par 50 ms), là où une version sans voix ou un autre montage diverge.
Les deux sont alignés au meilleur décalage (± 2 s) puis corrélés : 1 = identique, ~0 = sans rapport.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np


FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

LOW = (subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW) if sys.platform == "win32" else 0
VFPS = 4          # vignettes par seconde
AHOP = 0.05       # pas de l'enveloppe sonore (s)
MAX_LAG = 2.0     # décalage cherché (s)
SIDE = 16


def duration(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, creationflags=LOW).stdout.strip()
    return float(out or 0)


def probe(path: Path) -> dict:
    """Ce qu'Instagram regarde : codecs, taille d'image, débit, durée."""
    import json
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                          "format=duration,bit_rate,size:stream=codec_type,codec_name,width,height,sample_rate",
                          "-of", "json", str(path)], capture_output=True, text=True, creationflags=LOW).stdout
    d = json.loads(out or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    f = d.get("format", {})
    return {"vcodec": v.get("codec_name"), "w": v.get("width"), "h": v.get("height"), "acodec": a.get("codec_name"),
            "sr": int(a.get("sample_rate") or 0), "duration": float(f.get("duration") or 0),
            "bitrate": int(f.get("bit_rate") or 0), "size": int(f.get("size") or 0)}


def _frames(path: Path) -> np.ndarray:
    # recadrage central 80 % (les bords bougent avec les recadrages légers), vignette 16x16 en gris
    vf = f"fps={VFPS},crop=iw*0.8:ih*0.8,scale={SIDE}:{SIDE}:flags=area,format=gray"
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-an", "-vf", vf, "-f", "rawvideo", "-"],
                         capture_output=True, creationflags=LOW).stdout
    a = np.frombuffer(raw, np.uint8).astype(np.float32).reshape(-1, SIDE * SIDE)
    a = a - a.mean(axis=1, keepdims=True)
    n = np.linalg.norm(a, axis=1, keepdims=True)
    return np.where(n > 1e-3, a / np.maximum(n, 1e-3), 0).astype(np.float32)


def _envelope(path: Path) -> np.ndarray:
    sr = 8000
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, creationflags=LOW).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop = int(sr * AHOP)
    k = len(x) // hop
    if k == 0:
        return np.zeros(0, np.float32)
    rms = np.sqrt((x[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-9)
    return np.log10(rms + 1e-4).astype(np.float32)


def compute(path: Path) -> dict:
    return {"duration": duration(path), "frames": _frames(path), "env": _envelope(path)}


def _best_lag(score_at, max_lag: int) -> float:
    return max(score_at(lag) for lag in range(-max_lag, max_lag + 1))


def visual(a: np.ndarray, b: np.ndarray) -> float:
    """Similarité moyenne image par image (cosinus des vignettes), au meilleur décalage."""
    if len(a) < 4 or len(b) < 4:
        return 0.0

    def at(lag: int) -> float:
        x, y = (a[lag:], b) if lag >= 0 else (a, b[-lag:])
        n = min(len(x), len(y))
        if n < 4:
            return 0.0
        sims = (x[:n] * y[:n]).sum(axis=1)
        flat = (np.abs(x[:n]).sum(axis=1) < 1e-3) & (np.abs(y[:n]).sum(axis=1) < 1e-3)
        sims = np.where(flat, 1.0, sims)  # deux images unies (noir) = pareilles
        return float(sims.mean())

    return _best_lag(at, int(MAX_LAG * VFPS))


def audio(a: np.ndarray, b: np.ndarray) -> float:
    """Corrélation des enveloppes sonores, au meilleur décalage."""
    if len(a) < 20 or len(b) < 20:
        return 0.0

    def at(lag: int) -> float:
        x, y = (a[lag:], b) if lag >= 0 else (a, b[-lag:])
        n = min(len(x), len(y))
        if n < 20:
            return 0.0
        x, y = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
        d = float(np.linalg.norm(x) * np.linalg.norm(y))
        return float((x * y).sum() / d) if d > 1e-6 else 0.0

    return _best_lag(at, int(MAX_LAG / AHOP))


def compare(a: dict, b: dict) -> dict:
    return {"dur": round(abs(a["duration"] - b["duration"]), 2),
            "image": round(visual(a["frames"], b["frames"]), 3), "son": round(audio(a["env"], b["env"]), 3)}


def save(fp: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        np.savez_compressed(fh, duration=np.float32(fp["duration"]), frames=fp["frames"], env=fp["env"])


def load(path: Path) -> dict:
    with np.load(path) as z:
        return {"duration": float(z["duration"]), "frames": z["frames"], "env": z["env"]}


def image_vec(path: Path) -> np.ndarray:
    """Une image (miniature YouTube…) passée par le même traitement qu'une vignette de vidéo."""
    vf = f"crop=iw*0.8:ih*0.8,scale={SIDE}:{SIDE}:flags=area,format=gray"
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-"],
                         capture_output=True, creationflags=LOW).stdout
    a = np.frombuffer(raw, np.uint8).astype(np.float32)
    a = a - a.mean()
    return a / max(float(np.linalg.norm(a)), 1e-3)


def best_frame(frames: np.ndarray, vec: np.ndarray) -> float:
    """Ressemblance de l'image avec la vignette la plus proche de la vidéo (1 = c'est une de ses images).
    Calibré le 9 oct. : miniature YouTube ↔ sa vidéo 0.96–1.00, ↔ les autres ≤ 0.72."""
    return float((frames @ vec).max()) if len(frames) and vec.size == frames.shape[1] else 0.0
