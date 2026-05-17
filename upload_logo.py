"""
Upload a local image file to Supabase Storage as the Piatto restaurant logo.

Usage:
    python upload_logo.py <path-to-image>

Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in .env
"""

import sys
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL        = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
BUCKET              = "assets"
OBJECT_NAME         = "piatto-logo.png"


def upload(local_path: str) -> str:
    path = Path(local_path)
    if not path.exists():
        print(f"Error: file not found: {local_path}")
        sys.exit(1)

    if not SUPABASE_URL:
        print("Error: SUPABASE_URL is not set in .env")
        sys.exit(1)
    if not SUPABASE_SERVICE_KEY:
        print("Error: SUPABASE_SERVICE_KEY is not set in .env")
        sys.exit(1)

    suffix = path.suffix.lower()
    content_type = "image/png" if suffix == ".png" else "image/jpeg"

    upload_url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{OBJECT_NAME}"
    data = path.read_bytes()

    req = urllib.request.Request(
        upload_url,
        data=data,
        method="POST",
        headers={
            "Authorization":  f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type":   content_type,
            "x-upsert":       "true",
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"Upload failed ({e.code}): {body}")
        sys.exit(1)

    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{OBJECT_NAME}"
    print(f"Upload successful (HTTP {status})")
    print(f"Public URL: {public_url}")
    print()
    print("Add this to your .env:")
    print(f"LOGO_URL={public_url}")
    return public_url


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python upload_logo.py <path-to-image>")
        sys.exit(1)
    upload(sys.argv[1])
