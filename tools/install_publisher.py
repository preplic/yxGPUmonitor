#!/usr/bin/python3
"""Install an optional 15-minute user timer after Git authentication is ready."""

import argparse
from pathlib import Path
import shlex
import subprocess

from export_data import PRIVATE_CONFIG, REPO


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PRIVATE_CONFIG))
    args = parser.parse_args()
    private = Path(args.config).expanduser().resolve()
    if not private.exists():
        parser.error("Run tools/configure.py first")
    command = "exec /usr/bin/python3 %s --config %s --push" % (shlex.quote(str(REPO / "tools/publish.py")), shlex.quote(str(private)))
    quoted = '"' + command.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    (units / "yxgpu-publish.service").write_text(
        "[Unit]\nDescription=Publish encrypted YX GPU dashboard snapshot\n\n[Service]\nType=oneshot\n"
        "WorkingDirectory=%s\nExecStart=/bin/bash -ic %s\nUMask=0077\nTimeoutStartSec=300\n" % (str(REPO).replace("%", "%%"), quoted))
    (units / "yxgpu-publish.timer").write_text(
        "[Unit]\nDescription=Publish GPU dashboard every 15 minutes\n\n[Timer]\n"
        "OnCalendar=*:0/15\nPersistent=true\nRandomizedDelaySec=30\n\n[Install]\nWantedBy=timers.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "yxgpu-publish.timer"], check=True)
    print("15-minute publisher enabled; inspect journalctl --user -u yxgpu-publish.service")


if __name__ == "__main__":
    main()
