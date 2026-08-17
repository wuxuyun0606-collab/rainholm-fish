#!/usr/bin/env python3
"""Rainholm Fish one-command launcher.

Creates local User/AI keys on first run, serves the web client and API from one
origin, and prints ready-to-paste invitations for a phone player and an AI agent.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import webbrowser
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
SERVER_DIR = ROOT / "server"
DEFAULT_DATA_DIR = SERVER_DIR
GENERATED_SLOTS = ("user", "ai_guest")


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Start Rainholm Fish for a browser user and an AI fishing partner."
    )
    parser.add_argument(
        "--lan",
        action="store_true",
        help="listen on the local network so a phone on the same Wi-Fi can join",
    )
    parser.add_argument("--host", help="advanced: explicit listen address")
    parser.add_argument("--port", type=int, default=5210)
    parser.add_argument(
        "--public-base-url",
        help="public HTTPS site root used in the AI invitation, e.g. https://pond.example.com",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="directory for private keys and pond_save.json (default: server/)",
    )
    parser.add_argument("--no-open", action="store_true", help="do not open the local browser")
    return parser.parse_args()


def _load_or_create_tokens(path):
    tokens = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                tokens.update(loaded)
        except (OSError, ValueError):
            raise SystemExit("Cannot read %s; fix or remove that private file first." % path)

    changed = False
    for slot in GENERATED_SLOTS:
        value = tokens.get(slot)
        if not isinstance(value, str) or not value or value == "CHANGE_ME":
            tokens[slot] = secrets.token_urlsafe(32)
            changed = True

    if changed or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(tmp), flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(tokens, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        os.replace(str(tmp), str(path))
        os.chmod(str(path), 0o600)
    return tokens


def _lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def _site_root(value):
    root = value.rstrip("/")
    if root.endswith("/api/pond"):
        root = root[: -len("/api/pond")]
    return root


def _load_app():
    sys.path.insert(0, str(SERVER_DIR))
    spec = importlib.util.spec_from_file_location("rainholm_server", SERVER_DIR / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


def _print_invites(user_url, ai_base, ai_key, public_ready):
    print("\nRainholm Fish is ready.\n")
    print("User / phone:")
    print("  %s" % user_url)
    print("\nAI fishing partner:")
    print("  Base URL: %s" % ai_base)
    print("  Pond key: %s" % ai_key)
    print("\nPaste this into an AI chat that can make HTTP requests:\n")
    print("  Join my fishing pond. Use base URL %s and send the key" % ai_base)
    print("  %s in the X-Pond-Key header. First GET /ai/brief," % ai_key)
    print("  then POST /join. You may chat, fish, and poll for new messages.")
    if not public_ready:
        print("\n  Note: this address is local. A cloud-only chat AI needs a public HTTPS")
        print("  URL or tunnel; restart with --public-base-url after you have one.")
    print("\nPrivate keys are stored locally and ignored by Git. Press Ctrl-C to stop.\n")


def main():
    args = _parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")

    data_dir = args.data_dir.expanduser().resolve()
    token_path = data_dir / ".tokens.json"
    save_path = data_dir / "pond_save.json"
    tokens = _load_or_create_tokens(token_path)

    os.environ["RAINHOLM_TOKENS_PATH"] = str(token_path)
    os.environ["RAINHOLM_SAVE_PATH"] = str(save_path)

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    local_root = "http://127.0.0.1:%d" % args.port
    phone_host = _lan_ip() if host in ("0.0.0.0", "::") else host
    phone_root = "http://%s:%d" % (phone_host, args.port)

    public_root = _site_root(args.public_base_url) if args.public_base_url else ""
    if public_root:
        os.environ["RAINHOLM_PUBLIC_BASE"] = public_root + "/api/pond"
    ai_root = public_root or (phone_root if args.lan else local_root)

    user_url = "%s/tang-web/?key=%s" % (phone_root, quote(tokens["user"], safe=""))
    ai_base = ai_root + "/api/pond"
    _print_invites(user_url, ai_base, tokens["ai_guest"], bool(public_root))

    if not args.no_open:
        local_user_url = "%s/tang-web/?key=%s" % (
            local_root,
            quote(tokens["user"], safe=""),
        )
        threading.Timer(0.8, lambda: webbrowser.open(local_user_url)).start()

    app = _load_app()
    app.run(host=host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
