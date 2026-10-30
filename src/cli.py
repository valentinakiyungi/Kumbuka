"""`kumbuka` command: run Kumbuka on your own computer.

    kumbuka                 start (asks for a Gemini key the first time) and open the browser
    kumbuka --demo          start without a key, in offline demo mode
    kumbuka --set-key       replace the saved Gemini key
    kumbuka --port 9000     use a specific port
    kumbuka --no-browser    don't open a browser tab

Data (chats, memories, your key) lives in ~/.kumbuka, or $KUMBUKA_HOME if set.
"""
from __future__ import annotations

import argparse
import getpass
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

VERSION = "2.1.0"
KEY_URL = "https://aistudio.google.com/apikey"


def home_dir() -> Path:
    return Path(os.environ.get("KUMBUKA_HOME") or Path.home() / ".kumbuka").expanduser()


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_key(path: Path, key: str) -> None:
    env = read_env(path)
    env["GEMINI_API_KEY"] = key
    path.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    try:
        path.chmod(0o600)  # the key is a secret: readable by you only
    except OSError:
        pass


def ask_for_key() -> str:
    print("\nKumbuka uses Google Gemini. Get a free API key here:")
    print(f"  {KEY_URL}\n")
    try:
        return getpass.getpass("Paste your key (input is hidden; press Enter to use demo mode): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def free_port(start: int, host: str) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port found between {start} and {start + 49}. Try --port.")


def open_when_ready(url: str) -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "health", timeout=1) as r:
                if r.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.25)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kumbuka", description="Run Kumbuka, the AI that remembers you, on this computer.")
    ap.add_argument("--port", type=int, default=8765, help="port to serve on (default 8765, or the next free one)")
    ap.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1: this computer only)")
    ap.add_argument("--demo", action="store_true", help="run without a Gemini key (templated replies)")
    ap.add_argument("--set-key", action="store_true", help="enter a new Gemini API key")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--version", action="version", version=f"kumbuka {VERSION}")
    args = ap.parse_args(argv)

    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    env_file = home / ".env"
    saved = read_env(env_file)

    key = os.environ.get("GEMINI_API_KEY") or saved.get("GEMINI_API_KEY", "")
    if args.set_key or (not key and not args.demo and sys.stdin.isatty()):
        new = ask_for_key()
        if new:
            write_key(env_file, new)
            key = new
            print(f"Saved to {env_file}")
        elif args.set_key:
            raise SystemExit("No key entered; nothing changed.")

    # Settings from ~/.kumbuka/.env, without overriding the real environment.
    for k, v in saved.items():
        os.environ.setdefault(k, v)
    if key:
        os.environ["GEMINI_API_KEY"] = key
    os.environ.setdefault("KUMBUKA_DB", str(home / "kumbuka.db"))
    os.environ.setdefault("QDRANT_PATH", str(home / "qdrant"))
    if args.demo or not key:
        os.environ["LLM_PROVIDER"] = "demo"

    port = free_port(args.port, args.host)
    shown_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    url = f"http://{shown_host}:{port}/"

    # Import only after the environment is ready: configuration is read at import time.
    import uvicorn

    from src.api import app

    mode = "demo mode (no API key)" if os.environ.get("LLM_PROVIDER") == "demo" else "Gemini"
    print(f"\n  Kumbuka {VERSION} is running at {url}")
    print(f"  Model: {mode} · Data: {home}")
    print("  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    try:
        uvicorn.run(app, host=args.host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    print("Kwaheri!")


if __name__ == "__main__":
    main()
