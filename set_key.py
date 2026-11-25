"""Saves ONE secret into .env safely. The value is hidden while you paste it and never printed.

    python3 set_key.py api_id
    python3 set_key.py api_hash
    python3 set_key.py gemini
    python3 set_key.py bot_token
"""
import re
import sys
from getpass import getpass
from pathlib import Path

from dotenv import set_key

ENV = Path(__file__).resolve().parent / ".env"

KEYS = {
    "api_id": ("TELEGRAM_API_ID", r"\d{4,12}", "api_id = أرقام فقط (بلا : وبلا حروف)"),
    "api_hash": ("TELEGRAM_API_HASH", r"[0-9a-f]{32}", "api_hash = 32 حرف ورقم (a-f و 0-9)"),
    "gemini": ("GEMINI_API_KEY", r"(AIza|AQ\.)[A-Za-z0-9_\-\.]{25,}", "مفتاح Gemini كيبدا بـ AIza أو AQ."),
    "bot_token": ("BOT_TOKEN", r"\d{6,}:[A-Za-z0-9_\-]{30,}", "Token البوت فيه أرقام ثم : ثم حروف"),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in KEYS:
        print("استعمل: python3 set_key.py api_id | api_hash | gemini | bot_token")
        sys.exit(1)
    env_name, pattern, hint = KEYS[sys.argv[1]]
    if "--clipboard" in sys.argv:
        # Take the value straight from what you copied (Mac clipboard). Nothing is shown.
        import subprocess
        value = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
        if re.fullmatch(pattern, value):
            set_key(str(ENV), env_name, value, quote_mode="never")
            print(f"✅ تحفظ {env_name} مزيان (من الحافظة).")
            return
        kind = "فارغة" if not value else f"فيها {len(value)} حرف، كتبدا بـ '{value[:4]}'"
        print(f"❌ اللي منسوخ دابا ماشي هو ({kind}). {hint}.")
        sys.exit(1)
    for _ in range(3):
        value = getpass(f"لصق {sys.argv[1]} هنا (ما غاديش يبان) ثم Enter: ").strip()
        if re.fullmatch(pattern, value):
            set_key(str(ENV), env_name, value, quote_mode="never")
            print(f"✅ تحفظ {env_name} مزيان.")
            return
        print(f"❌ هادا ماشي هو. {hint}. عاود جرب.")
    print("ما تحفظ والو.")
    sys.exit(1)


if __name__ == "__main__":
    main()
