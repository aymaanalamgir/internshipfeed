"""Optional local PostgreSQL launcher for machines without Docker/PostgreSQL."""
import argparse
import signal
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Start development-only PostgreSQL (install .[local-postgres])")
    parser.add_argument("--data-dir", type=Path, default=Path("data/postgres"))
    args = parser.parse_args()
    import pgserver
    from dotenv import set_key
    from .db import ROOT

    server = pgserver.get_server(args.data_dir.resolve(), cleanup_mode="stop")
    # pgserver exposes a Unix socket by default, not a public TCP listener.
    env = ROOT / ".env"
    if not env.exists():
        set_key(str(env), "DATABASE_URL", server.get_uri())
        print("Wrote local DATABASE_URL to .env", flush=True)
    else:
        print("Existing .env preserved. Local server connection: " + server.get_uri(), flush=True)
    print("Local PostgreSQL ready. Keep this terminal running; Ctrl+C stops the server. Data is retained.", flush=True)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        stopped.wait()
    finally:
        server.cleanup()


if __name__ == "__main__":
    main()
