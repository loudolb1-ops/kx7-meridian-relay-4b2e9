"""Créneaux de publication (heure de Paris), sans aucune dépendance : le workflow s'en sert pour savoir, avant
d'installer quoi que ce soit, s'il y a un créneau à servir (GitHub ne connaît que l'heure UTC, d'où deux
déclenchements par créneau — été et hiver — dont un seul trouve le créneau à servir).

python robot/slots.py            code 0 s'il y a un créneau à servir, 1 sinon
python robot/slots.py --report   code 0 si le rapport complet du jour (JARVIS) reste à faire
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
IG = ("08:00", "12:30", "17:30", "20:30")      # Instagram (+ Facebook)
FB_ONLY: tuple = ()  # plus de créneau Facebook seul : Facebook = mêmes créneaux et même quota qu'Insta (Lou, 10 oct.)
SLOTS = tuple(sorted(IG + FB_ONLY))
# Pause (Lou, 13 oct. ~15:50 : « on met en pause le planning de post […] de tous les comptes, jusqu'à ce que je sache
# si j'ai été exclu ou non du programme de monétisation […] décale tout le planning à demain ») : aucun créneau servi
# ces jours-là ; les vidéos Facebook déjà programmées reculent d'autant de jours (robot.py fbshift)
PAUSE: tuple = ("2026-10-13",)
LATE_H = 3  # un créneau raté de plus de 3 h est sauté (pas de rafale de posts)
FILE = Path(__file__).resolve().parents[1] / "state" / "slots.json"


def now() -> datetime:
    return datetime.now(TZ)


def current(t: datetime | None = None) -> datetime | None:
    """Le dernier créneau passé, s'il date de moins de LATE_H heures."""
    t = t or now()
    for day in (t.date(), t.date() - timedelta(days=1)):
        for s in reversed(SLOTS):
            hh, mm = map(int, s.split(":"))
            st = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
            if st <= t:
                return st if t - st <= timedelta(hours=LATE_H) else None
    return None


def key(s: datetime) -> str:
    return s.strftime("%Y-%m-%d %H:%M")


def due() -> datetime | None:
    s = current()
    last = json.loads(FILE.read_text(encoding="utf-8")).get("last_slot") if FILE.exists() else None
    if s and s.strftime("%Y-%m-%d") in PAUSE:
        return None
    return s if s and last != key(s) else None


def report_due() -> bool:
    """Le rapport complet du matin : une fois par jour, à partir de 06:00 (Paris)."""
    db = FILE.with_name("report_db.json")
    done = json.loads(db.read_text(encoding="utf-8")).get("full_day") if db.exists() else None
    return now().hour >= 6 and done != now().strftime("%Y-%m-%d")


if __name__ == "__main__":
    if "--report" in sys.argv:
        ok = report_due()
        print("rapport du jour à faire" if ok else "rapport du jour déjà fait")
        raise SystemExit(0 if ok else 1)
    s = due()
    print(f"créneau à servir : {key(s)}" if s else "aucun créneau à servir")
    raise SystemExit(0 if s else 1)
