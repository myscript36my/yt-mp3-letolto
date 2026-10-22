# YouTube → MP3 letöltő

Egyszerű, ablakos program YouTube-videók hangjának letöltéséhez (MP3 320 kb/s, MP3 192 kb/s vagy eredeti minőség újrakódolás nélkül).

## Letöltés

A **Releases** részben (jobb oldalt) töltsd le a `YT-MP3-Letolto.exe` fájlt, és indítsd el dupla kattintással. Telepíteni nem kell semmit.

- Az első indításkor a program letölti az ffmpeg-et és a Deno-t (kb. 150 MB, csak egyszer). Ezek ide kerülnek: `%LOCALAPPDATA%\YT-MP3-Letolto`.
- A Windows SmartScreen figyelmeztethet („A Windows megvédte a számítógépet”), mert az exe nincs aláírva. Kattints a **További információ → Futtatás mindenképp** gombra.
- Ha a letöltés egyszer csak nem működik, kattints a **🔄 Frissítés** gombra. A YouTube gyakran változtat, és ez frissíti a letöltő motort.

## Új exe készítése

Minden `main` ágra küldött változtatásnál a GitHub Actions automatikusan új `.exe`-t fordít és tesztel, majd új kiadást készít.

Csak olyan tartalmat tölts le, amihez jogod van.
