#!/usr/bin/env python3
"""Offline restore. Existing data is retained in a sibling directory."""
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from datetime import datetime
import app


def restore(source, target):
    os.umask(0o077)
    source=Path(source).expanduser().resolve()
    target=Path(target).resolve()
    if not source.is_file():
        raise ValueError('Sicherungsdatei nicht gefunden.')
    with tempfile.TemporaryDirectory(dir=target.parent) as td:
        temp=Path(td)
        with zipfile.ZipFile(source) as z:
            members=z.infolist()
            if sum(m.file_size for m in members)>20*1024**3:
                raise ValueError('Sicherung über 20 GB: Wiederherstellung bitte manuell durchführen.')
            names=[m.filename for m in members]
            if len(names)!=len(set(names)):
                raise ValueError('Doppelte Dateinamen in der Sicherung.')
            for m in members:
                p=Path(m.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in m.filename:
                    raise ValueError('Unsicherer Pfad in der Sicherung.')
                if m.filename!='crm.sqlite3' and not (len(p.parts)==2 and p.parts[0]=='Dokumente' and len(p.parts[1])==48 and all(x in '0123456789abcdef' for x in p.parts[1])):
                    raise ValueError('Unerwartete Datei in der Sicherung: '+m.filename)
            if 'crm.sqlite3' not in names:
                raise ValueError('Die Datenbank fehlt in der Sicherung.')
            z.extractall(temp)
        c=sqlite3.connect(temp/'crm.sqlite3')
        try:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Die Datenbank ist beschädigt.')
            schema=c.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
            if not schema or schema[0]!='1':
                raise ValueError('Die Sicherung gehört zu einer anderen CRM-Version.')
            if c.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Ungültige Verknüpfungen in der Sicherung.')
            for storage,size in c.execute('SELECT storage_name,size FROM documents'):
                path=temp/'Dokumente'/storage
                if not path.is_file() or path.stat().st_size!=size:
                    raise ValueError('Ein Dokument fehlt oder ist beschädigt.')
        finally:
            c.close()
        old=None
        if target.exists():
            old=target.with_name(target.name+'-vor-Wiederherstellung-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
            if old.exists():
                raise ValueError('Sicherungsordner existiert bereits. Bitte später erneut versuchen.')
            target.rename(old)
        try:
            shutil.copytree(temp,target)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            if old:
                old.rename(target)
            raise
        return old


def main():
    print('\nCRM-Datensicherung wiederherstellen\n')
    print('Bitte das CRM vorher vollständig beenden (Strg+C im Startfenster).')
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',8765))==0:
            raise ValueError('Port 8765 ist noch belegt. Bitte das CRM zuerst schließen.')
    path=input('Pfad zur CRM-Sicherungsdatei (.zip): ').strip().strip("'\"")
    print('Ziel:',app.DATA)
    print('Die aktuellen Daten werden in einen separaten Ordner verschoben und bleiben erhalten.')
    if input('Zur Bestätigung WIEDERHERSTELLEN eingeben: ').strip()!='WIEDERHERSTELLEN':
        print('Abgebrochen.');return
    old=restore(path,app.DATA)
    print('Wiederherstellung erfolgreich. Das Passwort aus der Sicherung gilt wieder.')
    if old:
        print('Bisherige Daten:',old)
    print('Starte jetzt Start-CRM.command.')

if __name__=='__main__':
    try:
        main()
    except (ValueError,OSError,zipfile.BadZipFile,sqlite3.Error) as exc:
        print('Wiederherstellung fehlgeschlagen:',exc)
        raise SystemExit(1)
