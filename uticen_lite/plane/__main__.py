from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path


def launch_banner(host: str, port: int) -> str:
    url = f"http://{host}:{port}"
    return (
        f"Uticen Lite — {url}\n"
        f"  launch with:  controlplane   (or)   python -m uticen_lite.plane"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="controlplane")
    parser.add_argument("--project", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    import uvicorn

    from uticen_lite.plane.app import create_app

    app = create_app(Path(args.project))
    if not args.no_browser:
        webbrowser.open(f"http://{args.host}:{args.port}")
    print(launch_banner(args.host, args.port))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
