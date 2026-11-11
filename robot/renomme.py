"""Comptes renommés par Lou : ancien pseudo → pseudo actuel, dans TOUT l'état du robot.

11 nov. 2026 : « Maths Expliqués » s'appelle « Contre Calcul » sur les réseaux aussi
(TikTok @maths.expliques → @contre.calcul, YouTube @MathsExplique → @contrecalcul). L'ancien pseudo TikTok
ne répond plus : sans ce renommage le robot ne lisait plus le compte, donc plus aucune vidéo « sortie ».

`applique()` remplace le pseudo dans les fichiers d'état (comptes suivis, base du rapport, reposts, publiés).
Sans effet quand c'est déjà fait : appelé au début de chaque passage (report.py, robot.py), pour qu'un état
écrit par un passage lancé AVANT la mise à jour soit corrigé au suivant. Nouveau renommage = une ligne ici.
"""
from __future__ import annotations

from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state"

# (ancien, nouveau) : texte exact du pseudo, tel qu'il est écrit dans les fichiers
RENOMMES = (
    ("maths.expliques", "contre.calcul"),   # TikTok
    ("MathsExplique", "contrecalcul"),      # YouTube
)
FICHIERS = ("report_accounts.json", "todo_accounts.json", "report_db.json", "reposts.json", "published_ig.json",
            "published_fb.json", "ig_plan.json", "slots.json", "fb_best.json", "fails.json", "durations.json")


def applique() -> list:
    faits = []
    for name in FICHIERS:
        p = STATE / name
        if not p.exists():
            continue
        s = p.read_text(encoding="utf-8")
        t = s
        for a, b in RENOMMES:
            t = t.replace(a, b)
        if t != s:
            p.write_text(t, encoding="utf-8")
            faits.append(name)
    if faits:
        print("comptes renommés dans : " + ", ".join(faits), flush=True)
    return faits


def canon(who: str) -> str:
    for a, b in RENOMMES:
        if (who or "").lower() == a.lower():
            return b
    return who
