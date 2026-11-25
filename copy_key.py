"""Copies ONE value from .env to the Mac clipboard (nothing is printed), so you can paste it.

    python3 copy_key.py BOT_TOKEN
    python3 copy_key.py GEMINI_API_KEY
"""
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

name = sys.argv[1] if len(sys.argv) > 1 else ""
value = (dotenv_values(Path(__file__).resolve().parent / ".env").get(name) or "").strip()
if not value:
    sys.exit(f"❌ {name} فارغ ولا ما كاينش فـ .env")
subprocess.run(["pbcopy"], input=value, text=True, check=True)
print(f"✅ تنسخ {name}. دابا لصقو فخانة Secret بـ ⌘ + V")
