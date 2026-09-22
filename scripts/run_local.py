import argparse
import os
import sys

from common import ROOT

sys.path.insert(0, str(ROOT))
from changeguard.web import create_app  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Credential-free, loopback-only offline preview.")
    parser.add_argument("--port", type=int, default=8033)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    origin = f"http://127.0.0.1:{args.port}"
    os.environ.update(
        {
            "CHANGEGUARD_MODE": "local-fixture",
            "PUBLIC_ORIGIN": origin,
        }
    )
    print(f"Offline preview at {origin}; no operator login or Azure connection. Live start is disabled.")
    create_app().run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
