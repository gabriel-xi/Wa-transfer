"""
api.py — Pywebview JS bridge (v2.4 — Desktop)

Espone al frontend le stesse operazioni del vecchio server HTTP,
ora come metodi diretti senza porta di rete aperta.
"""

import shutil, zipfile, threading
from pathlib import Path

import webview

from engine import (
    Progress, adb_check, adb_devices, adb_device_info,
    run_transfer, run_transfer_auto, run_transfer_manual, run_transfer_crypt14,
    restore_clean_backup, get_clean_backup_info,
    TEMP, SAFETY_BACKUP_DIR,
)
from ios_merge import backup_info as ios_backup_info, run_ios_merge

IOS_MERGE_TEMP = TEMP / "ios_merge"

# ── Progress globale ──────────────────────────────────────────────────────────

_progress      = Progress()
_progress_lock = threading.Lock()

def _get_progress() -> Progress:
    with _progress_lock: return _progress

def _set_progress(p: Progress):
    global _progress
    with _progress_lock: _progress = p

def _uploaded_media_dir():
    d = TEMP / "uploads" / "media"
    if d.exists() and any(d.rglob("*")): return d
    return None


# ══════════════════════════════════════════════════════════════════════════════

class Api:
    """Classe esposta a JS via window.pywebview.api.*"""

    def __init__(self):
        self._window: webview.Window | None = None

    def set_window(self, window: webview.Window):
        self._window = window

    # ── Stato ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        devs      = adb_devices()
        media_dir = _uploaded_media_dir()
        return {
            "adb":            adb_check(),
            "device_count":   len(devs),
            "safety_backups": len(list(SAFETY_BACKUP_DIR.glob("*.sqlite"))),
            "media_uploaded": media_dir is not None,
            "media_files":    sum(1 for _ in media_dir.rglob("*") if _.is_file()) if media_dir else 0,
        }

    def get_devices(self) -> list:
        return [adb_device_info(d) for d in adb_devices()]

    def get_progress(self) -> dict:
        return _get_progress().to_dict()

    def get_clean_backup_info(self) -> dict:
        info = get_clean_backup_info()
        return info if info else {"available": False}

    # ── File dialog: selezione file locali ───────────────────────────────────

    def pick_db(self) -> dict:
        """Apre dialog nativo → copia msgstore.db in temp/uploads/."""
        if not self._window:
            return {"error": "Finestra non disponibile"}
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("SQLite DB (*.db *.sqlite)", "Tutti i file (*.*)")
        )
        if not paths:
            return {"error": "Nessun file selezionato"}
        src = Path(paths[0])
        if src.stat().st_size < 1024:
            return {"error": "File troppo piccolo"}
        with open(src, "rb") as f:
            header = f.read(16)
        if not header.startswith(b"SQLite format 3"):
            return {"error": "Non è un SQLite valido"}
        ud = TEMP / "uploads"; ud.mkdir(exist_ok=True)
        dest = ud / "msgstore.db"
        shutil.copy2(src, dest)
        return {"ok": True, "path": str(dest), "size": dest.stat().st_size, "name": src.name}

    def pick_crypt14(self) -> dict:
        """Apre dialog nativo → copia crypt14 in temp/uploads/."""
        if not self._window:
            return {"error": "Finestra non disponibile"}
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("Crypt (*.crypt14 *.crypt15 *.crypt12 *.crypt)", "Tutti i file (*.*)")
        )
        if not paths:
            return {"error": "Nessun file selezionato"}
        src = Path(paths[0])
        if src.stat().st_size < 1024:
            return {"error": "File troppo piccolo"}
        ud = TEMP / "uploads"; ud.mkdir(exist_ok=True)
        dest = ud / "msgstore.db.crypt14"
        shutil.copy2(src, dest)
        return {"ok": True, "path": str(dest), "size": dest.stat().st_size, "name": src.name}

    def pick_key(self) -> dict:
        """Apre dialog nativo → copia key in temp/uploads/."""
        if not self._window:
            return {"error": "Finestra non disponibile"}
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("Key WhatsApp (key *)", "Tutti i file (*.*)")
        )
        if not paths:
            return {"error": "Nessun file selezionato"}
        src = Path(paths[0])
        ud = TEMP / "uploads"; ud.mkdir(exist_ok=True)
        dest = ud / "key"
        shutil.copy2(src, dest)
        return {"ok": True, "size": dest.stat().st_size}

    def pick_media_zip(self) -> dict:
        """Apre dialog nativo → estrae zip media in temp/uploads/media/."""
        if not self._window:
            return {"error": "Finestra non disponibile"}
        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("ZIP (*.zip)", "Tutti i file (*.*)")
        )
        if not paths:
            return {"error": "Nessun file selezionato"}
        src = Path(paths[0])
        if not zipfile.is_zipfile(str(src)):
            return {"error": "Non è uno zip valido"}
        ud = TEMP / "uploads"; ud.mkdir(exist_ok=True)
        media_dest = ud / "media"
        if media_dest.exists():
            shutil.rmtree(media_dest)
        media_dest.mkdir(parents=True)
        with zipfile.ZipFile(str(src), "r") as zf:
            zf.extractall(str(media_dest))
        file_count  = sum(1 for _ in media_dest.rglob("*") if _.is_file())
        total_size  = sum(f.stat().st_size for f in media_dest.rglob("*") if f.is_file())
        return {
            "ok":      True,
            "files":   file_count,
            "size_mb": round(total_size / (1024 * 1024), 1),
            "path":    str(media_dest),
        }

    # ── Pipeline ─────────────────────────────────────────────────────────────

    def start(self, serial: str) -> dict:
        """Pipeline ADB standard."""
        if not serial:
            return {"error": "Nessun dispositivo"}
        p = Progress(); _set_progress(p)
        threading.Thread(target=run_transfer, args=(serial, p), daemon=True).start()
        return {"started": True}

    def start_auto(self, serial: str = "", gdrive_email: str = "",
                   gdrive_password: str = "", gdrive_phone: str = "") -> dict:
        """Pipeline automatica: ADB → Google Drive → fallback."""
        if not serial and not gdrive_email:
            return {"error": "Fornisci serial Android o email Google"}
        p = Progress(); _set_progress(p)
        threading.Thread(
            target=run_transfer_auto,
            args=(serial or None, p),
            kwargs={
                "gdrive_email":    gdrive_email    or None,
                "gdrive_password": gdrive_password or None,
                "gdrive_phone":    gdrive_phone    or None,
            },
            daemon=True
        ).start()
        return {"started": True, "serial": serial, "gdrive": bool(gdrive_email)}

    def start_uploaded(self) -> dict:
        """Pipeline da DB importato manualmente."""
        db_path = TEMP / "uploads" / "msgstore.db"
        if not db_path.exists():
            return {"error": "Nessun DB caricato"}
        media_dir = _uploaded_media_dir()
        p = Progress(); _set_progress(p)
        threading.Thread(
            target=run_transfer_manual,
            args=(str(db_path), p),
            kwargs={"media_dir": str(media_dir) if media_dir else None},
            daemon=True
        ).start()
        return {"started": True, "media": media_dir is not None}

    def start_crypt14(self) -> dict:
        """Pipeline da crypt14 + key."""
        crypt_path = TEMP / "uploads" / "msgstore.db.crypt14"
        key_path   = TEMP / "uploads" / "key"
        if not crypt_path.exists(): return {"error": "crypt14 non caricato"}
        if not key_path.exists():   return {"error": "key non caricata"}
        media_dir = _uploaded_media_dir()
        p = Progress(); _set_progress(p)
        threading.Thread(
            target=run_transfer_crypt14,
            args=(str(crypt_path), str(key_path), p),
            kwargs={"media_dir": str(media_dir) if media_dir else None},
            daemon=True
        ).start()
        return {"started": True, "media": media_dir is not None}

    def restore_original(self) -> dict:
        """Ripristina il backup iPhone pre-modifica."""
        info = get_clean_backup_info()
        if not info:
            return {"error": "Nessuna copia backup disponibile"}
        p = Progress(); _set_progress(p)
        threading.Thread(target=restore_clean_backup, args=(p,), daemon=True).start()
        return {"started": True}

    # ── iOS merge ────────────────────────────────────────────────────────────

    def pick_ios_backup(self, slot: str) -> dict:
        """
        Apre dialog per selezionare una cartella backup iOS (slot = 'a' | 'b').
        Verifica che contenga Manifest.db e ChatStorage.sqlite di WhatsApp.
        """
        if not self._window:
            return {"error": "Finestra non disponibile"}
        paths = self._window.create_file_dialog(
            webview.FOLDER_DIALOG,
            allow_multiple=False,
        )
        if not paths:
            return {"error": "Nessuna cartella selezionata"}

        backup_dir = Path(paths[0])
        info = ios_backup_info(backup_dir)

        if not info.get("valid"):
            return {
                "error": (
                    "Cartella non valida: Manifest.db non trovato.\n"
                    "Percorso tipico: ~/Library/Application Support/MobileSync/Backup/<UUID>"
                )
            }

        if not info.get("has_whatsapp"):
            return {"error": info.get("wa_error", "WhatsApp non trovato nel backup")}

        # Salva il percorso per start_ios_merge
        slots_dir = IOS_MERGE_TEMP / "slots"
        slots_dir.mkdir(parents=True, exist_ok=True)
        (slots_dir / f"backup_{slot}.txt").write_text(str(backup_dir), encoding="utf-8")

        return {
            "ok":          True,
            "path":        str(backup_dir),
            "device_name": info.get("device_name", "iPhone"),
            "ios_version": info.get("ios_version", "?"),
            "last_backup": info.get("last_backup", "?"),
            "file_count":  info.get("file_count", 0),
        }

    def start_ios_merge(self) -> dict:
        """Pipeline merge iOS↔iOS: fonde ChatStorage.sqlite di A in B."""
        slots_dir = IOS_MERGE_TEMP / "slots"
        file_a    = slots_dir / "backup_a.txt"
        file_b    = slots_dir / "backup_b.txt"

        if not file_a.exists():
            return {"error": "Backup A non selezionato"}
        if not file_b.exists():
            return {"error": "Backup B non selezionato"}

        backup_a = file_a.read_text(encoding="utf-8").strip()
        backup_b = file_b.read_text(encoding="utf-8").strip()

        p = Progress()
        _set_progress(p)
        threading.Thread(
            target=run_ios_merge,
            args=(backup_a, backup_b, p, IOS_MERGE_TEMP),
            daemon=True,
        ).start()
        return {"started": True}

    def reset_ios_merge(self) -> dict:
        """Cancella lo stato del merge iOS (selezioni backup)."""
        slots_dir = IOS_MERGE_TEMP / "slots"
        if slots_dir.exists():
            shutil.rmtree(slots_dir, ignore_errors=True)
        _set_progress(Progress())
        return {"ok": True}

    # ── Utility ──────────────────────────────────────────────────────────────

    def reset(self) -> dict:
        ud = TEMP / "uploads"
        if ud.exists():
            shutil.rmtree(ud, ignore_errors=True)
        _set_progress(Progress())
        return {"ok": True}

    def cleanup(self) -> dict:
        cleaned = 0
        for item in TEMP.iterdir():
            if item.name == "safety_backups":
                continue
            try:
                shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
                cleaned += 1
            except Exception:
                pass
        return {"cleaned": cleaned}
