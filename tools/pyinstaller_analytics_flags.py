#!/usr/bin/env python3
"""Print PyInstaller hidden-import flags for analytics modules. No secrets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
flags = ['--hidden-import', 'analytics', '--hidden-import', 'analytics_config']
if (ROOT / 'analytics_config_local.py').is_file():
    flags.extend(['--hidden-import', 'analytics_config_local'])
print(' '.join(flags))
