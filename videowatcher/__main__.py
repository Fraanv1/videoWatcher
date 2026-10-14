"""videoWatcher command line.

    python -m videowatcher                      interactive menu (Spanish); what a double-click runs
    python -m videowatcher <folder>             convert an SD card folder (drag a folder onto the .exe)
    python -m videowatcher adduser <name>       create or reset an app login
    python -m videowatcher serve [--host H] [--port P] [--open]
    python -m videowatcher convert <sd-card-folder> <output-folder>
"""

from __future__ import annotations

import argparse
import getpass
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .auth import UserStore

COMMANDS = {"adduser", "serve", "convert", "-h", "--help"}


def _ask_new_user(users: UserStore) -> bool:
    print("\nPrimero creá tu usuario para entrar a videoWatcher.")
    name = input("Usuario: ").strip()
    while True:
        password = getpass.getpass("Contraseña (mínimo 8 caracteres, no se ve al escribir): ")
        if password != getpass.getpass("Repetí la contraseña: "):
            print("Las contraseñas no coinciden. Probá de nuevo.")
            continue
        try:
            users.set_password(name or "admin", password)
        except ValueError:
            print("La contraseña es muy corta. Probá de nuevo.")
            continue
        print(f"Usuario '{name or 'admin'}' creado.")
        return True


def _serve(host: str, port: int, secure_cookie: bool, open_browser: bool) -> None:
    import uvicorn

    from .app import create_app

    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    print(f"\nvideoWatcher abierto en {url}  (cerrá esta ventana para salir)\n")
    uvicorn.run(create_app(secure_cookie=secure_cookie), host=host, port=port, log_level="warning")


def _convert(source: Path, output: Path | None) -> int:
    from .sdcard import convert_folder

    if not source.is_dir():
        print(f"No encuentro la carpeta: {source}")
        return 1
    if output is None:
        output = Path.home() / "Desktop" / f"videoWatcher_{source.name or 'tarjeta'}_{time.strftime('%Y%m%d_%H%M%S')}"
    print(f"\nOriginales (solo lectura): {source}\nVideos MP4 en:             {output}\n")
    failures = convert_folder(source, output)
    print("\nListo." if not failures else f"\nTerminado con {failures} archivo(s) con error (ver hashes_sha256.csv).")
    return 1 if failures else 0


def _pause() -> None:
    if getattr(sys, "frozen", False):
        input("\nPresioná Enter para cerrar...")


def menu() -> int:
    users = UserStore()
    print("=" * 56)
    print("  videoWatcher: grabaciones de cámaras iCSee / XMEye")
    print("=" * 56)
    print("  1) Abrir videoWatcher (buscar y descargar por red local)")
    print("  2) Convertir videos de la tarjeta microSD a MP4")
    print("  3) Salir")
    choice = input("\nElegí una opción (1/2/3): ").strip()
    if choice == "1":
        if not users.exists():
            _ask_new_user(users)
        _serve("127.0.0.1", 8765, False, True)
        return 0
    if choice == "2":
        print("\nArrastrá la carpeta de la tarjeta (o de su copia) a esta ventana y presioná Enter.")
        print("Ejemplo: E:\\   o   C:\\Evidencia\\camara1")
        raw = input("Carpeta: ").strip().strip('"')
        code = _convert(Path(raw), None) if raw else 1
        _pause()
        return code
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return menu()
    if argv[0] not in COMMANDS and Path(argv[0]).is_dir():
        code = _convert(Path(argv[0]), None)
        _pause()
        return code

    parser = argparse.ArgumentParser(prog="videowatcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("adduser", help="create or reset an app login")
    add.add_argument("username")

    serve = sub.add_parser("serve", help="run the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="open the browser")
    serve.add_argument("--secure-cookie", action="store_true",
                       help="mark the session cookie Secure (use behind HTTPS)")

    conv = sub.add_parser("convert", help="convert .h264 files from an SD card folder to MP4 (+ SHA-256 list)")
    conv.add_argument("source", type=Path)
    conv.add_argument("output", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "convert":
        return _convert(args.source, args.output)

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
    _serve(args.host, args.port, args.secure_cookie, args.open)
    return 0


if __name__ == "__main__":
    sys.exit(main())
