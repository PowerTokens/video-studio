#!/usr/bin/env python3
"""Confirm analytics modules are inside a PyInstaller binary. No secrets."""
import pathlib
import re
import subprocess
import sys

if len(sys.argv) != 2:
    sys.stderr.write("usage: check_analytics_bundle.py <pyinstaller-binary>\n")
    sys.exit(2)

binary = sys.argv[1]
out = subprocess.check_output(
    [sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer",
     "--list", "--recursive", binary],
    text=True, stderr=subprocess.STDOUT,
)
required = ["analytics", "analytics_config"]
if pathlib.Path("analytics_config_local.py").is_file():
    required.append("analytics_config_local")
missing = []
for name in required:
    if not re.search(r"(^|[^A-Za-z0-9_])" + re.escape(name) + r"([^A-Za-z0-9_]|$)", out):
        missing.append(name)
if missing:
    sys.stderr.write("analytics modules missing from the bundle: %s\n" % ", ".join(missing))
    sys.exit(1)
print("analytics_modules_ok", " ".join(required))
