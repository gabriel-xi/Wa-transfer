"""
ios_merge.py — Merge di due backup iOS non cifrati (solo WhatsApp)

Scenario d'uso:
  Backup A = iPhone con chat WhatsApp iOS originali (prima di qualsiasi trasferimento)
  Backup B = iPhone dopo il trasferimento ufficiale WhatsApp da Android

  I backup devono essere NON CIFRATI (Finder → iPhone → deseleziona "Cifra backup locale").
  WhatsApp su iOS salva ChatStorage.sqlite in chiaro dentro il backup → niente key, niente root.

Flusso:
  1. Trova ChatStorage.sqlite in entrambi i backup tramite Manifest.db
  2. Copia B come base, fonde i messaggi di A non presenti in B
  3. Re-inietta il DB unificato nel backup B (sovrascrive il file hash)
  4. L'utente ripristina il Backup B su iPhone tramite Finder
"""

import hashlib
import sqlite3
import shutil
import plistlib
from pathlib import Path

# Domain e percorso relativo di WhatsApp all'interno del backup iOS
WHATSAPP_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
WA_DB_RELPATH   = "ChatStorage.sqlite"

# fileID deterministico: SHA1("domain-relativePath") — indipendente dal contenuto
WA_FILE_ID = hashlib.sha1(
    (WHATSAPP_DOMAIN + "-" + WA_DB_RELPATH).encode()
).hexdigest()

# Percorso standard dei backup iOS su macOS
MOBILESYNC_DIR = Path.home() / "Library" / "Application Support" / "MobileSync" / "Backup"


# ─── Logging compatibile con Progress di engine ───────────────────────────────

def _plog(p, msg: str, level: str = "info") -> None:
    """
    Appende una voce di log all'oggetto Progress.
    Compatibile con qualsiasi interfaccia Progress ragionevole.
    """
    entry = {"msg": msg, "type": level}
    if callable(getattr(p, "log", None)):
        try:
            p.log(msg, level)
            return
        except TypeError:
            try:
                p.log(msg)
                return
            except Exception:
                pass
    for attr in ("_log", "log"):
        obj = getattr(p, attr, None)
        if isinstance(obj, list):
            obj.append(entry)
            return


# ─── Lettura backup iOS ───────────────────────────────────────────────────────

def find_wa_db(backup_dir: Path) -> Path:
    """
    Restituisce il percorso assoluto di ChatStorage.sqlite dentro il backup iOS.

    Il fileID è deterministico: SHA1("domain-relativePath"), indipendente dal
    contenuto del file. Non serve leggere Manifest.db per trovarlo.
    Verifichiamo comunque l'esistenza fisica del file.
    """
    if not (backup_dir / "Manifest.db").exists():
        raise FileNotFoundError(
            f"Manifest.db non trovato in:\n{backup_dir}\n\n"
            "Percorso tipico su macOS:\n"
            "~/Library/Application Support/MobileSync/Backup/<UUID>"
        )

    path = backup_dir / WA_FILE_ID[:2] / WA_FILE_ID
    if not path.exists():
        # Fallback: cerca in Manifest.db (per backup più vecchi con hash diverso)
        path = _find_wa_db_via_manifest(backup_dir)

    return path


def _find_wa_db_via_manifest(backup_dir: Path) -> Path:
    """Fallback: cerca ChatStorage.sqlite tramite Manifest.db."""
    con = sqlite3.connect(str(backup_dir / "Manifest.db"))
    try:
        row = con.execute(
            "SELECT fileID FROM Files WHERE domain=? AND relativePath=?",
            (WHATSAPP_DOMAIN, WA_DB_RELPATH),
        ).fetchone()
    finally:
        con.close()

    if not row:
        raise FileNotFoundError(
            "ChatStorage.sqlite non trovato nel backup.\n\n"
            "Verifica che:\n"
            "• WhatsApp fosse installato quando hai eseguito il backup\n"
            "• Il backup NON sia cifrato (Finder → iPhone → deseleziona 'Cifra backup locale')"
        )

    file_id = row[0]
    path    = backup_dir / file_id[:2] / file_id
    if not path.exists():
        raise FileNotFoundError(
            f"File hash {file_id[:16]}... non trovato.\n"
            "Il backup potrebbe essere incompleto o corrotto."
        )
    return path


def list_backups() -> list:
    """
    Scansiona ~/Library/Application Support/MobileSync/Backup/ e
    restituisce la lista di tutti i backup iOS trovati, ordinata per data
    (più recente prima). Ogni elemento è il dict di backup_info().
    """
    if not MOBILESYNC_DIR.exists():
        return []

    results = []
    try:
        candidates = [d for d in MOBILESYNC_DIR.iterdir() if d.is_dir()]
    except PermissionError:
        return []

    for d in candidates:
        info = backup_info(d)
        if info.get("valid"):
            results.append(info)

    # Ordina per data backup (più recente prima)
    results.sort(key=lambda x: x.get("last_backup", ""), reverse=True)
    return results


def backup_info(backup_dir: Path) -> dict:
    """
    Restituisce un dict con informazioni sul backup iOS:
    valid, device_name, ios_version, last_backup, file_count, has_whatsapp, wa_error.
    """
    result: dict = {"valid": False, "path": str(backup_dir)}

    manifest = backup_dir / "Manifest.db"
    if not manifest.exists():
        return result

    result["valid"] = True

    # Info.plist — dispositivo, versione iOS, data backup
    info_plist = backup_dir / "Info.plist"
    if info_plist.exists():
        try:
            with open(info_plist, "rb") as f:
                pl = plistlib.load(f)
            result["device_name"] = pl.get("Device Name", "iPhone")
            result["ios_version"] = pl.get("Product Version", "?")
            d = pl.get("Last Backup Date")
            result["last_backup"] = str(d)[:10] if d else "?"
            result["serial"]      = pl.get("Serial Number", "")
        except Exception:
            result.setdefault("device_name", "iPhone")

    # Numero di file nel backup
    try:
        con = sqlite3.connect(str(manifest))
        cnt = con.execute("SELECT COUNT(*) FROM Files").fetchone()[0]
        con.close()
        result["file_count"] = cnt
    except Exception:
        pass

    # Verifica presenza WhatsApp
    try:
        find_wa_db(backup_dir)
        result["has_whatsapp"] = True
    except Exception as exc:
        result["has_whatsapp"] = False
        result["wa_error"]     = str(exc)

    return result


# ─── Helpers SQLite ───────────────────────────────────────────────────────────

def _tbl_exists(con: sqlite3.Connection, name: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _max_pk(con: sqlite3.Connection, table: str) -> int:
    try:
        row = con.execute(f"SELECT MAX(Z_PK) FROM {table}").fetchone()
        return row[0] or 0
    except Exception:
        return 0


def _cols(con: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _fix_primarykey(con: sqlite3.Connection) -> None:
    """
    Aggiorna la tabella Z_PRIMARYKEY (metadata CoreData) ai valori reali.
    Necessario per evitare conflitti quando WhatsApp riscrive il database.
    """
    if not _tbl_exists(con, "Z_PRIMARYKEY"):
        return
    tbl_map = {
        "ChatSession": ("ZWACHATSESSION", "Z_PK"),
        "Message":     ("ZWAMESSAGE",     "Z_PK"),
        "MediaItem":   ("ZWAMEDIAITEM",   "Z_PK"),
    }
    for name, cur_max in con.execute("SELECT Z_NAME, Z_MAX FROM Z_PRIMARYKEY"):
        if name in tbl_map:
            tbl, col = tbl_map[name]
            if _tbl_exists(con, tbl):
                actual = con.execute(f"SELECT MAX({col}) FROM {tbl}").fetchone()[0] or 0
                if actual > cur_max:
                    con.execute(
                        "UPDATE Z_PRIMARYKEY SET Z_MAX=? WHERE Z_NAME=?", (actual, name)
                    )


# ─── Merge core ───────────────────────────────────────────────────────────────

def _merge_db(db_base: Path, db_src: Path, out: Path, p) -> dict:
    """
    Fonde i messaggi di db_src in db_base, scrive il risultato in out.

    Strategia:
    - db_base (Backup B) è la base: contiene le chat arrivate da Android
    - db_src  (Backup A) è la sorgente: contiene le vecchie chat iOS
    - Deduplicazione per ZSTANZAID (ID univoco messaggio assegnato da WhatsApp)
    - Chat matchate per ZCONTACTJID (JID WhatsApp = numero@s.whatsapp.net)
    - FK ZMEDIAITEM azzerata (i file media non sono trasferibili cross-backup)
    - Z_PRIMARYKEY aggiornata per CoreData
    """
    _plog(p, "Copia database base (Backup B)...")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(db_base), str(out))

    dst = sqlite3.connect(str(out))
    src = sqlite3.connect(str(db_src))

    try:
        dst.execute("PRAGMA journal_mode=WAL")
        dst.execute("PRAGMA foreign_keys=OFF")
        dst.execute("PRAGMA synchronous=NORMAL")

        # Statistiche pre-merge
        n_msg_before   = dst.execute("SELECT COUNT(*) FROM ZWAMESSAGE").fetchone()[0]
        n_chats_before = dst.execute("SELECT COUNT(*) FROM ZWACHATSESSION").fetchone()[0]
        _plog(p, f"Base: {n_msg_before:,} messaggi · {n_chats_before} chat")
        p.pct = 25

        # ── Indice STANZA IDs esistenti (deduplicazione) ─────────────────────
        _plog(p, "Indicizzazione messaggi esistenti (STANZA ID)...")
        existing_stanza: set = set(
            r[0] for r in dst.execute(
                "SELECT ZSTANZAID FROM ZWAMESSAGE WHERE ZSTANZAID IS NOT NULL"
            )
        )

        # ── Indice chat sessions esistenti ────────────────────────────────────
        existing_chats: dict = {}  # ZCONTACTJID → Z_PK in dst
        for pk, jid in dst.execute("SELECT Z_PK, ZCONTACTJID FROM ZWACHATSESSION"):
            if jid:
                existing_chats[jid] = pk

        max_chat_pk = _max_pk(dst, "ZWACHATSESSION")
        max_msg_pk  = _max_pk(dst, "ZWAMESSAGE")

        chat_cols = _cols(src, "ZWACHATSESSION")
        msg_cols  = _cols(src, "ZWAMESSAGE")

        # ── Merge ZWACHATSESSION ──────────────────────────────────────────────
        _plog(p, "Merge chat sessions...")
        src_chats   = src.execute("SELECT * FROM ZWACHATSESSION").fetchall()
        chat_pk_map: dict = {}  # old pk in src → new pk in dst
        new_chats   = 0

        for row in src_chats:
            d      = dict(zip(chat_cols, row))
            old_pk = d["Z_PK"]
            jid    = d.get("ZCONTACTJID")

            if jid and jid in existing_chats:
                # Chat già presente → riusa il PK esistente
                chat_pk_map[old_pk] = existing_chats[jid]
            else:
                # Nuova chat → inserisci con PK rimappata
                max_chat_pk        += 1
                new_pk              = max_chat_pk
                chat_pk_map[old_pk] = new_pk
                d["Z_PK"]          = new_pk
                d["ZLASTMESSAGE"]  = None   # sarà ricalcolato da WhatsApp

                cols_list = list(d.keys())
                vals      = [d[c] for c in cols_list]
                ph        = ",".join("?" * len(cols_list))
                try:
                    dst.execute(
                        f"INSERT OR IGNORE INTO ZWACHATSESSION"
                        f" ({','.join(cols_list)}) VALUES ({ph})",
                        vals,
                    )
                    new_chats += 1
                    if jid:
                        existing_chats[jid] = new_pk
                except Exception as exc:
                    _plog(p, f"  ⚠ Chat skip ({jid}): {exc}", "warn")

        dst.commit()
        _plog(p, f"Chat: +{new_chats} nuove sessioni aggiunte")
        p.pct = 45

        # ── Merge ZWAMESSAGE ──────────────────────────────────────────────────
        _plog(p, "Merge messaggi...")
        src_msgs  = src.execute("SELECT * FROM ZWAMESSAGE").fetchall()
        total_src = len(src_msgs)
        inserted  = 0
        dup       = 0
        no_chat   = 0
        BATCH     = 300

        ph_msg   = ",".join("?" * len(msg_cols))
        cols_str = ",".join(msg_cols)
        batch: list = []

        for i, row in enumerate(src_msgs):
            d       = dict(zip(msg_cols, row))
            stanza  = d.get("ZSTANZAID")
            old_cid = d.get("ZCHATSESSION")

            # Salta duplicati
            if stanza and stanza in existing_stanza:
                dup += 1
                continue

            # Salta messaggi senza chat mappata (non dovrebbe accadere)
            if old_cid not in chat_pk_map:
                no_chat += 1
                continue

            # Rimappa PK e FK
            max_msg_pk        += 1
            d["Z_PK"]          = max_msg_pk
            d["ZCHATSESSION"]  = chat_pk_map[old_cid]
            d["ZMEDIAITEM"]    = None   # media non trasferibile cross-backup

            if stanza:
                existing_stanza.add(stanza)

            batch.append([d.get(c) for c in msg_cols])

            if len(batch) >= BATCH:
                dst.executemany(
                    f"INSERT OR IGNORE INTO ZWAMESSAGE ({cols_str}) VALUES ({ph_msg})",
                    batch,
                )
                dst.commit()
                inserted += len(batch)
                batch     = []
                p.pct = 45 + int(i / max(total_src, 1) * 42)
                _plog(p, f"  Inseriti {inserted:,} / ~{total_src:,}...")

        if batch:
            dst.executemany(
                f"INSERT OR IGNORE INTO ZWAMESSAGE ({cols_str}) VALUES ({ph_msg})",
                batch,
            )
            dst.commit()
            inserted += len(batch)

        _plog(
            p,
            f"Messaggi: +{inserted:,} inseriti · "
            f"{dup:,} duplicati saltati · "
            f"{no_chat:,} senza chat",
        )
        p.pct = 90

        # ── Metadata CoreData ─────────────────────────────────────────────────
        _plog(p, "Aggiornamento Z_PRIMARYKEY (CoreData)...")
        _fix_primarykey(dst)
        dst.commit()

        n_msg_after   = dst.execute("SELECT COUNT(*) FROM ZWAMESSAGE").fetchone()[0]
        n_chats_after = dst.execute("SELECT COUNT(*) FROM ZWACHATSESSION").fetchone()[0]
        _plog(p, f"Totale finale: {n_msg_after:,} messaggi · {n_chats_after} chat")
        p.pct = 95

        return {
            "ok":          True,
            "merged_db":   str(out),
            "msg_before":  n_msg_before,
            "msg_after":   n_msg_after,
            "inserted":    inserted,
            "skipped_dup": dup,
            "new_chats":   new_chats,
            "chats_after": n_chats_after,
        }

    finally:
        dst.close()
        src.close()


def _inject(backup_dir: Path, merged_db: Path, p) -> None:
    """
    Sovrascrive ChatStorage.sqlite nel backup iOS con il database fuso.
    Il fileID è SHA1("domain-relativePath") → deterministico, nessuna
    lettura di Manifest.db necessaria.
    """
    _plog(p, "Reiniezione nel backup B...")
    dest = backup_dir / WA_FILE_ID[:2] / WA_FILE_ID
    if not dest.parent.exists():
        raise RuntimeError(
            f"Directory hash {WA_FILE_ID[:2]}/ non trovata nel backup B.\n"
            "Il backup potrebbe essere corrotto o non contenere WhatsApp."
        )
    shutil.copy2(str(merged_db), str(dest))
    _plog(p, "✓ ChatStorage.sqlite aggiornato nel backup B")


# ─── Pipeline principale (eseguita in thread) ─────────────────────────────────

def run_ios_merge(backup_a: str, backup_b: str, progress, temp_dir: Path) -> None:
    """
    Pipeline completa del merge iOS↔iOS.

    backup_a  = cartella backup con le vecchie chat iOS (sorgente, non viene modificato)
    backup_b  = cartella backup dopo trasferimento Android (base, verrà modificato)
    progress  = oggetto Progress da engine
    temp_dir  = cartella temp dove scrivere il DB fuso
    """
    try:
        progress.pct   = 2
        progress.msg   = "Avvio merge iOS↔iOS..."
        progress.done  = False
        progress.error = False

        ba = Path(backup_a)
        bb = Path(backup_b)

        _plog(progress, "=== Merge iOS↔iOS WhatsApp ===")
        _plog(progress, f"Backup A (sorgente): {ba.name}")
        _plog(progress, f"Backup B (base):     {bb.name}")

        _plog(progress, "Verifica Backup A...")
        progress.pct = 5
        db_a = find_wa_db(ba)
        _plog(progress, f"  ✓ ChatStorage.sqlite trovato ({db_a.stat().st_size // 1024:,} KB)")

        _plog(progress, "Verifica Backup B...")
        progress.pct = 10
        db_b = find_wa_db(bb)
        _plog(progress, f"  ✓ ChatStorage.sqlite trovato ({db_b.stat().st_size // 1024:,} KB)")

        progress.msg = "Merge database..."
        out_dir = temp_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        merged_out = out_dir / "ChatStorage_merged.sqlite"

        stats = _merge_db(db_b, db_a, merged_out, progress)

        progress.msg = "Reiniezione nel backup..."
        _inject(bb, merged_out, progress)

        progress.pct  = 100
        progress.msg  = "Merge completato ✓"
        progress.done = True
        if not hasattr(progress, "data") or progress.data is None:
            progress.data = {}
        progress.data["ios_merge_stats"] = stats
        _plog(progress, "=== Merge completato con successo ===")

    except Exception as exc:
        import traceback
        progress.error = True
        progress.done  = False
        progress.msg   = str(exc)
        _plog(progress, f"ERRORE: {exc}", "fail")
        _plog(progress, traceback.format_exc(), "fail")
