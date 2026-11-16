#!/usr/bin/python3
"""Keep the publishing password outside Git; never accept it on the command line."""

import argparse
import base64
import getpass
from pathlib import Path
import secrets

from export_data import PRIVATE_CONFIG, REPO, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PRIVATE_CONFIG))
    parser.add_argument("--monitor-config", default="auto")
    args = parser.parse_args()
    path = Path(args.config).expanduser().resolve()
    if path.is_relative_to(REPO):
        parser.error("Private configuration must be outside the repository")
    if path.exists():
        parser.error("Configuration already exists; update it privately to rotate credentials")
    password = getpass.getpass("Dashboard password: ")
    if len(password) < 8 or password != getpass.getpass("Repeat password: "):
        parser.error("Passwords must match and contain at least 8 characters")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(path, {"password": password, "salt": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
                       "monitor_config": args.monitor_config, "monitor_home": str(Path.home())})
    path.chmod(0o600)
    print("Private configuration created outside the repository: " + str(path))


if __name__ == "__main__":
    main()
