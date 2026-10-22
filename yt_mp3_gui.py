#!/usr/bin/env python3
"""
YouTube -> MP3 letöltő, grafikus felülettel.

Két módon futtatható:
  A) Kész .exe (Windows): dupla kattintás, semmi mást nem kell telepíteni.
     Első indításkor letölti az ffmpeg-et és a Deno-t (~150 MB, egyszer).
  B) Python-szkriptként (Python 3.10+): python yt_mp3_gui.py
     A hiányzó csomagokat pip-pel magától telepíti.
"""

import importlib
import json
import os
import queue
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

APP_NAME = "YT-MP3-Letolto"
FROZEN = getattr(sys, "frozen", False)          # True, ha .exe-ként fut
IS_WIN = sys.platform.startswith("win")
NO_WINDOW = 0x08000000 if IS_WIN else 0         # ne villanjon fel fekete ablak

# .exe ablakos módban nincs konzol: a print ne okozzon hibát
for _s in ("stdout", "stderr"):
    if getattr(sys, _s) is None:
        setattr(sys, _s, open(os.devnull, "w", encoding="utf-8"))

if IS_WIN:
    APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
else:
    APP_DIR = Path.home() / f".{APP_NAME.lower()}"
BIN_DIR = APP_DIR / "bin"          # ffmpeg.exe, ffprobe.exe, deno.exe
LIB_DIR = APP_DIR / "lib"          # frissített yt-dlp (csak .exe módban)

DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl.zip")
PIP_PACKAGES = ["yt-dlp[default]", "mutagen", "static-ffmpeg", "deno"]
UPDATABLE = ("yt-dlp", "yt-dlp-ejs")    # PyPI-név -> modul: yt_dlp, yt_dlp_ejs

DEFAULT_DIR = str(Path.home() / "Music" / "YouTube")
FORMATS = {
    "MP3 – 320 kb/s (bárhol lejátszható)": ("mp3", "320"),
    "MP3 – 192 kb/s (kisebb fájl)": ("mp3", "192"),
    "Eredeti hang, újrakódolás nélkül (legjobb minőség)": ("best", None),
}


# =====================================================================
# Frissített yt-dlp betöltése .exe módban
# A beépített yt-dlp elavulhat; a Frissítés gomb a LIB_DIR-be tölti az újat,
# és ez a "finder" gondoskodik róla, hogy azt használjuk a beépített helyett.
# =====================================================================
class _OverrideFinder:
    mods = {p.replace("-", "_") for p in UPDATABLE}

    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] not in self.mods:
            return None
        from importlib.machinery import PathFinder
        return PathFinder.find_spec(name, path if path is not None else [str(LIB_DIR)])


_override = None
if FROZEN and (LIB_DIR / "yt_dlp").is_dir():
    _override = _OverrideFinder()
    sys.meta_path.insert(0, _override)


def load_ytdlp(log):
    """yt_dlp importálása; ha a frissített változat hibás, visszaáll a beépítettre."""
    global _override
    try:
        import yt_dlp  # noqa: F401
        from yt_dlp.version import __version__
        return __version__
    except Exception as e:
        if not _override:
            raise
        log(f"⚠ A frissített yt-dlp hibás ({e}), visszaállok a beépítettre.")
        sys.meta_path.remove(_override)
        _override = None
        for m in [m for m in sys.modules if m.partition(".")[0] in _OverrideFinder.mods]:
            del sys.modules[m]
        shutil.rmtree(LIB_DIR, ignore_errors=True)
        import yt_dlp  # noqa: F401
        from yt_dlp.version import __version__
        return __version__


# =====================================================================
# Python / Tkinter ellenőrzése (szkript módban)
# =====================================================================
if sys.version_info < (3, 10):
    _msg = (f"Túl régi Python ({sys.version.split()[0]}). Legalább 3.10 kell.\n"
            "Töltsd le az újat: https://www.python.org/downloads/")
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk(); _r.withdraw(); _mb.showerror("Régi Python", _msg)
    except Exception:
        print(_msg)
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    print("Hiányzik a Tkinter (grafikus felület).\nLinuxon telepítsd:  sudo apt install python3-tk")
    sys.exit(1)


# =====================================================================
# Közös segédfüggvények
# =====================================================================
def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    return urllib.request.urlopen(req, timeout=timeout)


def download(url, dest, log, label):
    """Fájl letöltése százalékos kiírással a naplóba."""
    with http_get(url, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, -10
        while chunk := r.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                if pct >= last + 10:
                    last = pct
                    log(f"   {label}: {pct}%  ({done >> 20} / {total >> 20} MB)")


def ver_tuple(v):
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).replace("-", ".").split("."))


def script_dirs():
    """Ahová a pip a parancsokat (pl. deno.exe) teszi, plusz a saját bin mappánk."""
    dirs = {str(BIN_DIR), sysconfig.get_path("scripts") or "", str(Path(sys.executable).parent)}
    try:
        dirs.add(sysconfig.get_path("scripts", f"{os.name}_user"))
    except KeyError:
        pass
    return [d for d in dirs if d and os.path.isdir(d)]


def find_exe(name):
    return shutil.which(name, path=os.pathsep.join(script_dirs() + [os.environ.get("PATH", "")]))


def find_js_runtime():
    deno = find_exe("deno")
    if deno:
        return {"deno": {"path": deno}}, "Deno"
    node = find_exe("node")
    if node:
        return {"node": {"path": node}}, "Node.js"
    return None, None


# =====================================================================
# .exe mód: ffmpeg és Deno letöltése, yt-dlp frissítése PyPI-ról
# =====================================================================
def fetch_zip_members(url, wanted, log, label):
    """Letölt egy zip-et, és a 'wanted' nevű fájlokat (bárhol a zip-ben) a BIN_DIR-be teszi."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "dl.zip"
        log(f"{label} letöltése (csak első alkalommal)…")
        download(url, zpath, log, label)
        with zipfile.ZipFile(zpath) as z:
            found = set()
            for info in z.infolist():
                base = info.filename.rsplit("/", 1)[-1]
                if base in wanted:
                    with z.open(info) as src, open(BIN_DIR / base, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.add(base)
    missing = set(wanted) - found
    if missing:
        raise RuntimeError(f"a letöltött csomagból hiányzik: {', '.join(missing)}")


def ensure_tools_frozen(log):
    """ffmpeg + Deno: rendszerre telepített, ha van; különben letöltjük a BIN_DIR-be."""
    ffmpeg = find_exe("ffmpeg")
    if not (ffmpeg and find_exe("ffprobe")):
        if not IS_WIN:
            log("✖ Az ffmpeg hiányzik. Telepítsd a rendszer csomagkezelőjével.")
            return None
        try:
            fetch_zip_members(FFMPEG_URL, {"ffmpeg.exe", "ffprobe.exe"}, log, "ffmpeg")
        except Exception as e:
            log(f"✖ Az ffmpeg letöltése nem sikerült: {e}\n   Van internet? Próbáld újra a Frissítés gombbal.")
            return None
        ffmpeg = find_exe("ffmpeg")
    log("✔ ffmpeg")

    js, js_name = find_js_runtime()
    if not js and IS_WIN:
        try:
            fetch_zip_members(DENO_URL, {"deno.exe"}, log, "Deno")
            js, js_name = find_js_runtime()
        except Exception as e:
            log(f"⚠ A Deno letöltése nem sikerült: {e}")
    if js:
        log(f"✔ {js_name}")
    else:
        log("⚠ Nincs JavaScript-futtató (Deno) – a YouTube letöltés valószínűleg nem fog menni.")
    return {"ffmpeg_dir": str(Path(ffmpeg).parent), "js": js}


def pypi_latest(pkg):
    with http_get(f"https://pypi.org/pypi/{pkg}/json") as r:
        data = json.load(r)
    whl = next(u for u in data["urls"]
               if u["packagetype"] == "bdist_wheel" and u["filename"].endswith("-none-any.whl"))
    return data["info"]["version"], whl["url"]


def check_update_frozen():
    """Visszaadja az elérhető új yt-dlp verziót, vagy None-t."""
    from yt_dlp.version import __version__ as cur
    latest, _ = pypi_latest("yt-dlp")
    return latest if ver_tuple(latest) > ver_tuple(cur) else None


def update_frozen(log):
    """Legújabb yt-dlp + yt-dlp-ejs letöltése a LIB_DIR-be. True, ha újraindítás kell."""
    from yt_dlp.version import __version__ as cur
    latest, _ = pypi_latest("yt-dlp")
    if ver_tuple(latest) <= ver_tuple(cur):
        log(f"✔ A yt-dlp naprakész ({cur}).")
        return False
    log(f"yt-dlp frissítése: {cur} → {latest}")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    new_dir = Path(tempfile.mkdtemp(prefix="lib-", dir=APP_DIR))
    try:
        for pkg in UPDATABLE:
            ver, url = pypi_latest(pkg)
            whl = new_dir / "pkg.whl"
            download(url, whl, log, f"{pkg} {ver}")
            with zipfile.ZipFile(whl) as z:
                z.extractall(new_dir)
            whl.unlink()
        shutil.rmtree(LIB_DIR, ignore_errors=True)
        new_dir.rename(LIB_DIR)
    except Exception:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise
    log("✔ Frissítés kész. A program újraindul…")
    return True


# =====================================================================
# Szkript mód: pip-pel telepít mindent
# =====================================================================
def pip_install(packages, log, upgrade=False):
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    if upgrade:
        base.append("-U")
    last = [""]
    for extra in ([], ["--user"], ["--user", "--break-system-packages"]):
        r = subprocess.run(base + extra + packages, capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        if r.returncode == 0:
            return True
        last = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
    log(f"pip hiba: {last[0]}")
    return False


def ensure_pip(log):
    ok = lambda *a: subprocess.run([sys.executable, "-m", *a], capture_output=True,  # noqa: E731
                                   creationflags=NO_WINDOW).returncode == 0
    if ok("pip", "--version"):
        return True
    log("A pip hiányzik, telepítem…")
    return ok("ensurepip", "--upgrade")


def prepare_script(log, force_update):
    if not ensure_pip(log):
        log("✖ A pip nem telepíthető. Telepítsd újra a Pythont a python.org-ról.")
        return None
    missing = []
    for mod, pkg in (("yt_dlp", "yt-dlp[default]"), ("yt_dlp_ejs", "yt-dlp[default]"),
                     ("mutagen", "mutagen"), ("static_ffmpeg", "static-ffmpeg")):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if not find_exe("deno") and not find_exe("node"):
        missing.append("deno")
    if missing or force_update:
        pkgs = PIP_PACKAGES if force_update else sorted(set(missing))
        log(("Frissítés: " if force_update else "Hiányzó összetevők telepítése: ") + ", ".join(pkgs))
        if not pip_install(pkgs, log, upgrade=True):
            log("✖ Telepítési hiba. Van internet? Kézzel:  "
                f'"{sys.executable}" -m pip install -U ' + " ".join(f'"{p}"' for p in PIP_PACKAGES))
            return None
        importlib.invalidate_caches()
        if force_update and "yt_dlp" in sys.modules:
            log("✔ Frissítve. A program újraindul…")
            return "restart"
    try:
        log(f"✔ yt-dlp {load_ytdlp(log)}")
    except ImportError:
        log("✖ A yt-dlp nem tölthető be. Indítsd újra a programot.")
        return None

    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        ffmpeg_dir = str(Path(shutil.which("ffmpeg")).parent)
    else:
        try:
            from static_ffmpeg import run
            log("ffmpeg letöltése (első alkalommal kb. 1 perc)…")
            ffmpeg_dir = str(Path(run.get_or_fetch_platform_executables_else_raise()[0]).parent)
        except Exception as e:
            log(f"✖ ffmpeg nem érhető el: {e}")
            return None
    log("✔ ffmpeg")
    js, js_name = find_js_runtime()
    log(f"✔ {js_name}" if js else "⚠ Nincs JavaScript-futtató (Deno) – a YouTube letöltés "
        "valószínűleg nem fog menni.")
    return {"ffmpeg_dir": ffmpeg_dir, "js": js}


# =====================================================================
# Előkészítés (mindkét mód)
# =====================================================================
def prepare(log, force_update=False):
    """Visszaad: beállítás-dict, 'restart', vagy None hiba esetén."""
    if not FROZEN:
        return prepare_script(log, force_update)
    try:
        log(f"✔ yt-dlp {load_ytdlp(log)}")
    except Exception as e:
        log(f"✖ A yt-dlp nem tölthető be: {e}")
        return None
    if force_update:
        try:
            if update_frozen(log):
                return "restart"
        except Exception as e:
            log(f"⚠ A frissítés nem sikerült: {e}")
    cfg = ensure_tools_frozen(log)
    if cfg and not force_update:
        try:
            new = check_update_frozen()
            if new:
                log(f"ℹ Elérhető újabb yt-dlp ({new}). Ha gond van a letöltéssel, kattints a Frissítés gombra.")
        except Exception:
            pass
    return cfg


def restart_app():
    args = [sys.executable] + ([] if FROZEN else [os.path.abspath(sys.argv[0])])
    subprocess.Popen(args, close_fds=True)
    os._exit(0)


def build_ydl_opts(cfg, out_dir, fmt_label, logger=None, hook=None, post_hook=None):
    codec, quality = FORMATS[fmt_label]
    extract = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
    if quality:
        extract["preferredquality"] = quality
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "ignoreerrors": True,
        "overwrites": False,
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ffmpeg_location": cfg["ffmpeg_dir"],
        "postprocessors": [
            extract,
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail", "already_have_thumbnail": False},
        ],
    }
    if logger:
        opts["logger"] = logger
    if hook:
        opts["progress_hooks"] = [hook]
    if post_hook:
        opts["postprocessor_hooks"] = [post_hook]
    if cfg["js"]:
        opts["js_runtimes"] = cfg["js"]
    return opts


class GuiLogger:
    def __init__(self, put):
        self.put = put

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        msg = msg.replace("ERROR: ", "").strip()
        if "Sign in to confirm" in msg:
            msg += "\n   (A YouTube robotnak nézett. Próbáld később, vagy kevesebb videóval egyszerre.)"
        elif "Private video" in msg or "unavailable" in msg:
            msg += "\n   (A videó privát, törölt vagy nem elérhető a régiódban.)"
        self.put(("log", "✖ " + msg))


# =====================================================================
# Felület
# =====================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube → MP3 letöltő")
        self.geometry("660x540")
        self.minsize(540, 440)
        if FROZEN and IS_WIN:
            try:
                self.iconbitmap(default=sys.executable)
            except tk.TclError:
                pass
        self.msgs = queue.Queue()
        self.cfg = None
        self.busy = False
        self.done_count = 0
        self._build_ui()
        self.after(100, self._poll)
        self._run_prepare(force_update=False)

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="YouTube linkek (soronként egy, lejátszási lista is jó):",
                  font=("", 10, "bold")).pack(anchor="w", **pad)
        url_box = ttk.Frame(frm)
        url_box.pack(fill="x", padx=12)
        self.urls = tk.Text(url_box, height=5, wrap="none", undo=True)
        self.urls.pack(side="left", fill="x", expand=True)
        side = ttk.Frame(url_box)
        side.pack(side="left", padx=(6, 0), anchor="n")
        ttk.Button(side, text="Beillesztés", command=self._paste).pack(fill="x")
        ttk.Button(side, text="Törlés", command=lambda: self.urls.delete("1.0", "end")).pack(fill="x", pady=4)

        ttk.Label(frm, text="Formátum:", font=("", 10, "bold")).pack(anchor="w", **pad)
        self.fmt = tk.StringVar(value=list(FORMATS)[0])
        ttk.Combobox(frm, textvariable=self.fmt, values=list(FORMATS),
                     state="readonly").pack(fill="x", padx=12)

        ttk.Label(frm, text="Mentés helye:", font=("", 10, "bold")).pack(anchor="w", **pad)
        dir_box = ttk.Frame(frm)
        dir_box.pack(fill="x", padx=12)
        self.out_dir = tk.StringVar(value=DEFAULT_DIR)
        ttk.Entry(dir_box, textvariable=self.out_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(dir_box, text="Tallózás…", command=self._browse).pack(side="left", padx=(6, 0))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", padx=12, pady=12)
        self.dl_btn = ttk.Button(btns, text="⬇  Letöltés", command=self._start, state="disabled")
        self.dl_btn.pack(side="left")
        ttk.Button(btns, text="📂  Mappa megnyitása", command=self._open_folder).pack(side="left", padx=6)
        self.upd_btn = ttk.Button(btns, text="🔄  Frissítés", command=lambda: self._run_prepare(True))
        self.upd_btn.pack(side="right")

        self.status = tk.StringVar(value="Előkészítés…")
        ttk.Label(frm, textvariable=self.status).pack(anchor="w", padx=12)
        self.bar = ttk.Progressbar(frm, maximum=100)
        self.bar.pack(fill="x", padx=12, pady=(4, 8))

        self.log = tk.Text(frm, height=8, state="disabled", bg="#f4f4f4", wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _paste(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            return
        if text:
            if self.urls.get("1.0", "end").strip():
                self.urls.insert("end", "\n")
            self.urls.insert("end", text)

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get())
        if d:
            self.out_dir.set(d)

    def _open_folder(self):
        path = self.out_dir.get()
        os.makedirs(path, exist_ok=True)
        if IS_WIN:
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy):
        self.busy = busy
        self.upd_btn.configure(state="disabled" if busy else "normal")
        self.dl_btn.configure(state="disabled" if busy or not self.cfg else "normal")

    def _run_prepare(self, force_update):
        if self.busy:
            return
        self._set_busy(True)
        self.status.set("Frissítés keresése…" if force_update else "Összetevők ellenőrzése…")
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)

        def work():
            try:
                cfg = prepare(lambda m: self.msgs.put(("log", m)), force_update)
            except Exception as e:
                self.msgs.put(("log", f"✖ Váratlan hiba: {e}"))
                cfg = None
            self.msgs.put(("ready", cfg))
        threading.Thread(target=work, daemon=True).start()

    def _start(self):
        if self.busy or not self.cfg:
            return
        urls = [u.strip() for u in self.urls.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            messagebox.showinfo("Nincs link", "Előbb illessz be legalább egy YouTube linket.")
            return
        bad = [u for u in urls if not u.startswith(("http://", "https://"))]
        if bad:
            messagebox.showwarning("Hibás link", "Ez nem link:\n" + bad[0][:80])
            return
        try:
            os.makedirs(self.out_dir.get(), exist_ok=True)
        except OSError as e:
            messagebox.showerror("Mappa hiba", f"A mappa nem hozható létre:\n{e}")
            return
        self._set_busy(True)
        self.done_count = 0
        self.bar.configure(mode="determinate", value=0)
        threading.Thread(target=self._worker, args=(urls,), daemon=True).start()

    def _hook(self, d):
        info = d.get("info_dict") or {}
        title = (info.get("title") or Path(d.get("filename", "")).stem)[:50]
        idx, n = info.get("playlist_index"), info.get("n_entries")
        prefix = f"[{idx}/{n}] " if idx and n else ""
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = d.get("downloaded_bytes", 0) / total * 100 if total else 0
            self.msgs.put(("progress", pct, f"{prefix}Letöltés: {title}  ({pct:.0f}%)"))
        elif d["status"] == "finished":
            self.msgs.put(("progress", 100, f"{prefix}Átalakítás: {title}…"))

    def _post_hook(self, d):
        if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
            path = d.get("info_dict", {}).get("filepath", "")
            self.msgs.put(("log", "✔ " + Path(path).name))
            self.msgs.put(("count",))

    def _worker(self, urls):
        import yt_dlp
        opts = build_ydl_opts(self.cfg, self.out_dir.get(), self.fmt.get(),
                              GuiLogger(self.msgs.put), self._hook, self._post_hook)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                errors = ydl.download(urls)
            self.msgs.put(("done", bool(errors)))
        except Exception as e:
            self.msgs.put(("log", f"✖ Váratlan hiba: {e}"))
            self.msgs.put(("done", True))

    def _poll(self):
        try:
            while True:
                m = self.msgs.get_nowait()
                kind = m[0]
                if kind == "progress":
                    self.bar["value"] = m[1]
                    self.status.set(m[2])
                elif kind == "log":
                    self._write_log(m[1])
                elif kind == "count":
                    self.done_count += 1
                elif kind == "ready":
                    self.bar.stop()
                    self.bar.configure(mode="determinate", value=0)
                    if m[1] == "restart":
                        self.after(800, restart_app)
                        continue
                    self.cfg = m[1] or self.cfg
                    self._set_busy(False)
                    if m[1]:
                        self.status.set("Kész. Illessz be egy linket, majd kattints a Letöltésre.")
                    else:
                        self.status.set("Hiba az előkészítésben – lásd a naplót.")
                        messagebox.showerror("Hiba", "Valamelyik összetevő hiányzik.\nRészletek a naplóban.")
                elif kind == "done":
                    n = self.done_count
                    if m[1]:
                        text = f"Kész: {n} fájl. Néhány elem nem sikerült (lásd a naplót)."
                        self._write_log(text + "\n   Tipp: kattints a Frissítés gombra, "
                                        "a YouTube gyakran változtat valamin.")
                    else:
                        text = f"✅ Minden kész! {n} fájl elmentve."
                        self._write_log(text)
                    self.status.set(text)
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._poll)


# =====================================================================
# Önteszt (a GitHub build ellenőrzi vele az elkészült .exe-t)
#   YT-MP3-Letolto.exe --selftest eredmeny.txt
# =====================================================================
def selftest(out_path):
    lines = []
    log = lines.append
    code = 1
    try:
        import yt_dlp_ejs  # noqa: F401
        import mutagen     # noqa: F401
        log("✔ yt_dlp_ejs, mutagen beépítve")
        cfg = prepare(log)
        if cfg and cfg != "restart":
            code = 0
            log(f"cfg: {cfg}")
            import yt_dlp
            out = tempfile.mkdtemp()
            opts = build_ydl_opts(cfg, out, list(FORMATS)[0])
            opts["quiet"] = False
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download(["https://www.youtube.com/watch?v=jNQXAC9IVRw"])
                files = list(Path(out).glob("*.mp3"))
                log(f"PRÓBALETÖLTÉS: {'OK ' + files[0].name if files else 'nincs mp3 (YouTube blokkolhatta a szervert)'}")
            except Exception as e:
                log(f"PRÓBALETÖLTÉS hiba (nem végzetes): {e}")
    except Exception as e:
        log(f"✖ {type(e).__name__}: {e}")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return code


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    App().mainloop()
