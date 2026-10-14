"""videoWatcher command line.

    python -m videowatcher adduser <name>      create or reset an app login
    python -m videowatcher serve [--host H] [--port P]
    python -m videowatcher convert <sd-card-folder> <output-folder>
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .auth import UserStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="videowatcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("adduser", help="create or reset an app login")
    add.add_argument("username")

    serve = sub.add_parser("serve", help="run the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--secure-cookie", action="store_true",
                       help="mark the session cookie Secure (use behind HTTPS)")

    conv = sub.add_parser("convert", help="convert .h264 files from an SD card folder to MP4 (+ SHA-256 list)")
    conv.add_argument("source", type=Path)
    conv.add_argument("output", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "convert":
        from .sdcard import convert_folder

        if not args.source.is_dir():
            print(f"Folder not found: {args.source}", file=sys.stderr)
            return 1
        return 1 if convert_folder(args.source, args.output) else 0

    users = UserStore()

    if args.cmd == "adduser":
        password = getpass.getpass("Password (min 8 chars): ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
        try:
            users.set_password(args.username, password)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"User '{args.username}' saved.")
        return 0

    if not users.exists():
        print("No app users yet. Create one first:\n  python -m videowatcher adduser <name>", file=sys.stderr)
        return 1
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"Warning: listening on {args.host}; other machines on the network can reach the login page.")

    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(secure_cookie=args.secure_cookie), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
