    #!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Firebird Database Updater

A lightweight utility for updating Firebird databases through SQL migration
scripts, creating automatic backups, and deploying associated executables.

Features
--------
- Detects the current database version
- Applies pending SQL migration scripts sequentially
- Creates automatic backups
- Extracts the latest application package (.7z)
- Provides a simple Tkinter-based GUI
- Generates execution logs

Dependencies
------------
pip install fdb py7zr

Packaging
---------
pyinstaller --onefile --noconsole firebird_updater.py

Expected Structure:
    FirebirdUpdater/Files/System X/lastBuild.7z
    FirebirdUpdater/Files/System Y/lastBuild.7z
    FirebirdUpdater/Files/Scripts/lastBuild.sql
    
"""

import os
import re
import sys
import shutil
import datetime
import traceback

import fdb
import py7zr

APP_NAME = "firebird_updater"
LOG_FILE = "log_update.txt"


def get_base_dir():
    """
    Returns the correct "base" folder both in script mode and when packaged
    with PyInstaller (--onefile or --onedir).
    """
    if getattr(sys, 'frozen', False):
        # packaged executable
        try:
            return sys._MEIPASS  # resources packaged in the onefile
        except Exception:
            # in --onedir, the exe is inside the dist/<name> folder -> dirname(executable)
            return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))



# ------------------------ Log utilities ------------------------

def time_tag() -> str:
    return datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

def log_line(msg: str) -> str:
    return f"{time_tag()} {msg}\n"

# ------------------------ Firebird ------------------------

def connect_firebird(db_path: str, user: str, password: str):
    # Uses localhost:<path> for standard clients
    dsn = f"localhost:{db_path}"
    return fdb.connect(dsn=dsn, user=user, password=password)


def get_current_version(db_path: str, user: str, password: str, logger) -> str:
    logger("Connecting to database to get current version...")
    conn = connect_firebird(db_path, user, password)
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(VERSION COLUMN) FROM VERSION_TABLE")
        row = cur.fetchone()
        version = row[0] if row else None
        if version is None:
            logger("No version found in table; assuming '0'.")
            version = "0"
        logger(f"Current version detected: {version}")
        return str(version)
    finally:
        conn.close()

# ------------------------ Compare version ------------------------

_version_token_re = re.compile(r"(\d+(?:[._]\d+)*)")


def parse_version_tokens(text: str):
    """Extracts numeric tokens like (1, 2, 3) from 'v1.2.3' or '.166'.
    Returns tuple of integers to compare.
    """
    m = _version_token_re.search(text)
    if not m:
        return tuple()
    raw = m.group(1).replace('_', '.')
    return tuple(int(p) for p in raw.strip('.').split('.') if p.isdigit())


def version_greater(a: str, b: str) -> bool:
    ta, tb = parse_version_tokens(a), parse_version_tokens(b)
    return ta > tb

# ------------------------ Scripts ------------------------

def list_scripts(scripts_dir: str, current_version: str, logger):
    if not os.path.isdir(scripts_dir):
        logger(f"Scripts folder not found: {scripts_dir}")
        return []
    files = [f for f in os.listdir(scripts_dir) if f.lower().endswith('.sql')]
    # Sorts by version tokens found in the name
    files.sort(key=lambda n: parse_version_tokens(n))
    pending = [f for f in files if version_greater(f, current_version)]
    logger(f"Scripts detected ({len(pending)}): {pending}")
    return pending


def execute_scripts(db_path: str, user: str, password: str, scripts_dir: str, scripts: list, logger):
    if not scripts:
        logger("No scripts to execute.")
        logger("The system is already at the latest version")
        return
    conn = connect_firebird(db_path, user, password)
    cur = conn.cursor()
    try:
        for name in scripts:
            scpath = os.path.join(scripts_dir, name)
            logger(f"Executing script: {name}")
            try:
                with open(scpath, 'r', encoding='utf-8') as f:
                    sql = f.read()
            except Exception as e:
                logger(f"Failed to read {name}: {e}. Ignoring.")
                continue

            # Splits by ';' — adjust to ignore errors per statement
            stmts = [s.strip() for s in sql.split(';') if s.strip()]
            ok, fail = 0, 0
            for stmt in stmts:
                try:
                    cur.execute(stmt)
                    ok += 1
                except Exception as e:
                    # Ignores errors and continues
                    fail += 1
                    logger(f"  Ignored error: {str(e).strip()}")
            conn.commit()
            logger(f"  Script completed: {ok} commands OK, {fail} errors ignored.")
    finally:
        conn.close()

# ------------------------ Backups ------------------------

def backups(db_path: str, backups_dir: str, logger) -> str:
    os.makedirs(backups_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(db_path))[0]
    filename = f"{base}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.FDB"
    destiny = os.path.join(backups_dir, filename)
    logger(f"Creating backups: {destiny}")
    shutil.copy2(db_path, destiny)
    return destiny

# ------------------------ Executable (zipped) ------------------------

def find_exec_zip(base_dir: str, system: str, logger) -> str:
    """
    Locates the most recent .7z considering variations of where the bundle may be.
    Searches in possible folders, avoiding duplication like C:\FirebirdUpdater\FirebirdUpdater\Files...

    **  Searches for the latest deployment package
        across several possible installation layouts.

    """
    sub = "System X" if system == 'X' else "System Y"

    # possible locations
    candidates = [
        os.path.join(base_dir, 'FirebirdUpdater', 'Files', sub),
        os.path.join(base_dir, 'Files', sub),
        os.path.join(os.path.dirname(base_dir), 'FirebirdUpdater', 'Files', sub),
        os.path.join(base_dir, sub),
    ]

    if logger:
        logger(f"Looking for .7z of system '{system}' in candidate folders...")

    for folder in candidates:
        try:
            if os.path.isdir(folder):
                if logger:
                    logger(f"Using Files folder: {folder}")
                zips = [f for f in os.listdir(folder) if f.lower().endswith('.7z')]
                if not zips:
                    raise FileNotFoundError(f"No .7z found in {folder}")
                if len(zips) > 1 and logger:
                    logger("Warning: more than one .7z found; using the first in alphabetical order.")
                selected = sorted(zips)[0]
                zip_path = os.path.join(folder, selected)
                if logger:
                    logger(f"Selected executable file: {zip_path}")
                return zip_path
        except FileNotFoundError:
            # handles and continues searching in other candidates
            continue

    # if not found in any
    raise FileNotFoundError(f"Folder with .7z not found. Check the structure inside '{base_dir}'.")


def extract_zip(file_7z: str, destination_dir: str, logger):
    logger(f"Extracting {file_7z} to: {destination_dir}")
    with py7zr.SevenZipFile(file_7z, mode='r') as z:
        z.extractall(path=destination_dir)

# ------------------------ GUI (Tkinter) ------------------------

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title(f"{APP_NAME} — Database Updater")
    root.geometry("850x550")

    systems = ['System X', 'System Y']
    system_var = tk.StringVar(value=systems[0])
    dbpath_var = tk.StringVar()
    bkdir_var = tk.StringVar()

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill='both', expand=True)

    ttk.Label(frm, text=f"{APP_NAME} — Database Updater", font=('Segoe UI', 14, 'bold')).grid(row=0, column=0, columnspan=4, pady=(0,10), sticky='w')

    ttk.Label(frm, text="System Type:").grid(row=1, column=0, sticky='e')
    system_cb = ttk.Combobox(frm, values=systems, textvariable=system_var, state='readonly', width=20)
    system_cb.grid(row=1, column=1, sticky='w')

    ttk.Label(frm, text="Database File (.FDB):").grid(row=2, column=0, sticky='e')
    db_entry = ttk.Entry(frm, textvariable=dbpath_var, width=60)
    db_entry.grid(row=2, column=1, sticky='w')
    def browse_db():
        path = filedialog.askopenfilename(filetypes=[("Firebird Database", "*.fdb"), ("All Files", "*.*")])
        if path:
            dbpath_var.set(path)
    ttk.Button(frm, text="Browse...", command=browse_db).grid(row=2, column=2, sticky='w')

    ttk.Label(frm, text="backups Folder (optional):").grid(row=3, column=0, sticky='e')
    bk_entry = ttk.Entry(frm, textvariable=bkdir_var, width=60)
    bk_entry.grid(row=3, column=1, sticky='w')
    def browse_bk():
        path = filedialog.askdirectory()
        if path:
            bkdir_var.set(path)
    ttk.Button(frm, text="Choose...", command=browse_bk).grid(row=3, column=2, sticky='w')

    ttk.Separator(frm, orient='horizontal').grid(row=4, column=0, columnspan=4, sticky='ew', pady=10)

    start_btn = ttk.Button(frm, text="Start")
    start_btn.grid(row=5, column=0, pady=5)
    exit_btn = ttk.Button(frm, text="Exit", command=root.destroy)
    exit_btn.grid(row=5, column=1, pady=5, sticky='w')

    ttk.Separator(frm, orient='horizontal').grid(row=6, column=0, columnspan=4, sticky='ew', pady=10)
    ttk.Label(frm, text="Execution Log:").grid(row=7, column=0, columnspan=4, sticky='w')

    log_box = scrolledtext.ScrolledText(frm, width=100, height=20, font=('Consolas', 9), state='disabled')
    log_box.grid(row=8, column=0, columnspan=4, pady=(0,10))

    def append_log(text: str):
        log_box.configure(state='normal')
        log_box.insert('end', text)
        log_box.see('end')
        log_box.configure(state='disabled')
        root.update()

    def on_start():
        system_sel = system_var.get()
        system = 'System X' if 'X' in system_sel else 'System Y'
        db_path = dbpath_var.get()
        if not db_path:
            messagebox.showerror("Error", "Select the database .FDB file.")
            return
        if not os.path.isfile(db_path):
            messagebox.showerror("Error", "Invalid .FDB path.")
            return
        db_dir = os.path.dirname(db_path)
        backups_dir = bkdir_var.get().strip() or os.path.join(db_dir, 'backups')
        user = os.getenv("FB_USER", "SYSDBA")
        password = os.getenv("FB_PASSWORD", "masterkey")

        def logger(msg: str):
            line = log_line(msg)
            append_log(line)
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(line)

        start_btn.config(state='disabled')
        root.update()

        try:
            logger("=== UPDATE START ===")
            logger(f"Selected system: {system_sel}")
            try:
                backups_path = backups(db_path, backups_dir, logger)
                logger(f"backups created: {backups_path}")
            except Exception as e:
                logger(f"backups failed: {e}. Continuing anyway (as requested).")
            try:
                current_version = get_current_version(db_path, user, password, logger)
            except Exception as e:
                logger(f"Could not get current version ({e}). Assuming '0'.")
                current_version = '0'

            def find_scripts_folder(base_dir_local, logger_local):
                candidates = []
                base_name = os.path.basename(base_dir_local).lower()
                if base_name == 'FirebirdUpdater':
                    candidates.append(os.path.join(base_dir_local, 'Files', 'Scripts'))
                    candidates.append(os.path.join(base_dir_local, 'Scripts'))
                    candidates.append(os.path.join(os.path.dirname(base_dir_local), 'Files', 'Scripts'))
                else:
                    candidates.append(os.path.join(base_dir_local, 'FirebirdUpdater', 'Files', 'Scripts'))
                    candidates.append(os.path.join(base_dir_local, 'Files', 'Scripts'))
                    candidates.append(os.path.join(os.path.dirname(base_dir_local), 'FirebirdUpdater', 'Files', 'Scripts'))
                    candidates.append(os.path.join(base_dir_local, 'Scripts'))
                candidates = [os.path.normpath(p) for p in candidates]
                if logger_local:
                    logger_local(f"Searching for scripts folder among candidates: {candidates}")
                for p in candidates:
                    if os.path.isdir(p):
                        if logger_local:
                            logger_local(f"Scripts folder found and used: {p}")
                        return p
                if logger_local:
                    logger_local(f"Warning: no scripts folder found. Using first candidate: {candidates[0]}")
                return candidates[0]

            base_dir = get_base_dir()
            scripts_folder = find_scripts_folder(base_dir, logger)
            scripts = list_scripts(scripts_folder, current_version, logger)
            try:
                execute_scripts(db_path, user, password, scripts_folder, scripts, logger)
            except Exception as e:
                logger(f"General failure executing scripts: {e}. Continuing...")
            try:
                base_dir = get_base_dir()
                zip_exec = find_exec_zip(base_dir, system, logger)
                extract_zip(zip_exec, db_dir, logger)
            except Exception as e:
                logger(f"Failed to update executable: {e}. (continuing)")
            logger("=== UPDATE COMPLETED ===")
            messagebox.showinfo("Completed", "Update finished. Check the log for details.")
        except Exception as e:
            errmsg = f"UNEXPECTED ERROR: {e}\n{traceback.format_exc()}"
            logger(errmsg)
            messagebox.showerror("Error", "An error occurred. Check the log for details.")
        finally:
            start_btn.config(state='normal')

    start_btn.config(command=on_start)

    root.mainloop()

if __name__ == '__main__':
    run_gui()
