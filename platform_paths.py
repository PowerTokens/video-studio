"""Per-user folders and "show in the file manager" for Windows and macOS.

Standard library only. Windows paths stay on %LOCALAPPDATA% and Videos.
LOCALAPPDATA, when set to a non-empty value, still wins on every OS so tests
and "run without touching my real data" keep working.
"""
import os
import subprocess
import sys
from pathlib import Path

APP_DIR_NAME = 'PowerTokensWan'
OUTPUT_DIR_NAME = 'PowerTokensVideoStudio'


def user_data_dir():
    """Folder for task records, settings and the remembered-key marker.

    Windows: %LOCALAPPDATA%\\PowerTokensWan
    macOS: ~/Library/Application Support/PowerTokensWan
    other: ~/.local/share/PowerTokensWan
    """
    override = os.environ.get('LOCALAPPDATA')
    if override:
        root = Path(override)
    elif sys.platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support'
    else:
        root = Path.home() / '.local' / 'share'
    return root / APP_DIR_NAME


def default_output_dir():
    """Folder suggested for newly generated videos.

    Windows (and other non-Mac systems): ~/Videos/PowerTokensVideoStudio
    macOS: ~/Movies/PowerTokensVideoStudio
    """
    home = Path.home()
    if sys.platform == 'darwin':
        return home / 'Movies' / OUTPUT_DIR_NAME
    return home / 'Videos' / OUTPUT_DIR_NAME


def reveal_path(path):
    """Open a file or folder in the system file manager.

    Windows uses os.startfile, macOS uses `open`, other systems use `xdg-open`.
    Raises OSError when the launcher cannot be started.
    """
    path = os.fspath(path)
    try:
        if sys.platform == 'win32':
            os.startfile(path)  # noqa: S606 — Windows shell open
            return
        command = ['open', path] if sys.platform == 'darwin' else ['xdg-open', path]
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise OSError(exc.returncode, 'could not reveal %s' % path) from exc
