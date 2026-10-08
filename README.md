# Robot Instagram AXIOM

Tourne sur GitHub Actions, même PC éteint. 4 créneaux par jour (08:00, 12:30, 17:30, 20:30, heure de Paris) sur
@axiomscience.app + la Page Facebook : d'abord les vidéos postées par Lou sur ses comptes TikTok (relevé 1 fois par
jour, fichier d'origine pris dans OneDrive « AXIOM acquisition »), sinon un quiz.

- État lisible : OneDrive `AXIOM acquisition/_robot Insta (ne pas toucher)/État.txt`
- Lancer à la main : onglet Actions → robot-insta → Run workflow
- Comptes suivis : `state/reposts.json` → `accounts`
- Code : `robot/robot.py` (le reste est expliqué en tête de chaque fichier)
