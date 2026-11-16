#!/usr/bin/python3
"""Export real node-monitor observations as an authenticated encrypted snapshot."""

import argparse
import base64
import csv
import gzip
import importlib
import io
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import sys
import tempfile
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


PRIVATE_CONFIG = Path.home() / ".config/yxgpu-monitor/publish.json"
REPO = Path(__file__).resolve().parents[1]
ITERATIONS = 600_000
AAD = b"yxGPUmonitor:v1"


def read_json(path):
    with Path(path).open() as handle:
        return json.load(handle)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            name = handle.name
            json.dump(value, handle, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def encrypted(payload, password, salt):
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=ITERATIONS).derive(password.encode("utf-8"))
    nonce = secrets.token_bytes(12)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, gzip.compress(raw, mtime=0), AAD)
    return {"version": 1, "algorithm": "AES-256-GCM", "kdf": "PBKDF2-SHA256",
            "iterations": ITERATIONS, "compression": "gzip",
            "salt": base64.b64encode(salt).decode("ascii"),
            "iv": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii")}


def monitor_paths(private):
    override = private.get("monitor_config", "auto")
    system = Path("/etc/node-monitor/config.json")
    if override != "auto":
        path = Path(override)
    elif system.exists():
        path = system
    else:
        path = Path(private.get("monitor_home", str(Path.home()))) / ".config/node-monitor/config.json"
    cfg = read_json(path)
    code = private.get("monitor_code") or ("/opt/node-monitor" if path == system else
           str(Path(private.get("monitor_home", str(Path.home()))) / ".local/lib/node-monitor"))
    return cfg, code


def identity(row):
    return {"uid": row["uid"], "name": row.get("username") or "uid:%s" % row["uid"]}


def collector_status(state, problems):
    return {"running": state.get("running"), "startedAt": state.get("started_at"),
            "stoppedAt": state.get("stopped_at"), "lastSample": state.get("last_success_at"),
            "sampleCount": state.get("sample_count", 0),
            "intervalSeconds": state.get("interval_seconds"), "problems": problems}


def point(state):
    current = state.get("current", {})
    users = {}
    for owners in current.get("owners", {}).values():
        for key in owners:
            users[key] = users.get(key, 0) + 1
    return {"t": state["last_success_at"], "busy": len({p["gpu_uuid"] for p in current.get("processes", [])}),
            "cards": users, "totals": {key: row["gpu_seconds"] for key, row in state["users"].items()},
            "shared": {key: row["shared_gpu_seconds"] for key, row in state["users"].items()},
            "observed": state["observed_physical_seconds"],
            "unobserved": state["unobserved_physical_seconds"],
            "unattributed": state["unattributed_physical_seconds"]}


def history(cfg, state, common, now):
    buckets = {}
    first = None
    incidents = []
    for item in common.read_events(Path(cfg["gpu_root"]) / cfg["host"], now - 7 * 86400):
        kind = item["type"]
        if kind == "sample" and item.get("ledger_id") == state["ledger_id"] and item["last_success_at"] >= now - 7 * 86400:
            value = point(item)
            first = first or value
            width = 300 if value["t"] >= now - 86400 else 3600
            buckets[(width, int(value["t"] // width))] = value
        elif kind in ("start", "stop", "gap", "sample_error") and item["time"] >= now - 7 * 86400:
            incidents.append({"t": item["time"], "source": "gpu", "type": kind,
                              "message": item.get("error") or item.get("reason"),
                              "seconds": item.get("seconds")})
    points = [*buckets.values(), point(state)]
    if first:
        points.append(first)
    return sorted({p["t"]: p for p in points}.values(), key=lambda item: item["t"]), incidents


def gpu_metrics():
    result = subprocess.run(["/usr/bin/nvidia-smi",
        "--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,power.limit",
        "--format=csv,noheader,nounits"], check=True, capture_output=True, text=True, timeout=15)
    values = []
    for row in csv.reader(io.StringIO(result.stdout)):
        row = [part.strip() for part in row]
        def number(index):
            try:
                return float(row[index])
            except ValueError:
                return None
        values.append({"index": int(row[0]), "uuid": row[1], "model": row[2], "memoryTotal": number(3),
                       "memoryUsed": number(4), "utilization": number(5), "temperature": number(6),
                       "power": number(7), "powerLimit": number(8)})
    return values


def build_payload(private, now=None):
    now = now or time.time()
    cfg, code = monitor_paths(private)
    sys.path.insert(0, code)
    common = importlib.import_module("monitor_common")
    gpu = importlib.import_module("gpu_usage")
    state = read_json(Path(cfg["gpu_root"]) / cfg["host"] / "status.json")
    gpu_problems = gpu.health(state, now=now)
    samples, incidents = history(cfg, state, common, now)
    current = state.get("current", {})
    cards = gpu_metrics()
    for card in cards:
        owners = current.get("owners", {}).get(card["uuid"], {})
        card["owners"] = [identity(row) for row in owners.values()]
        card["processes"] = sum(p["gpu_uuid"] == card["uuid"] for p in current.get("processes", []))
        card["occupied"] = card["processes"] > 0
    accounts = private.get("allowed_users") or [row.pw_name for row in pwd.getpwall()
        if (row.pw_uid == 0 or 1000 <= row.pw_uid < 65534) and row.pw_shell not in
        ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false")]
    accounts = [*accounts, *private.get("extra_allowed_users", [])]
    return {"schema": 1, "generatedAt": now, "publishInterval": 900, "allowedUsers": sorted(set(accounts)),
            "node": {"name": cfg["host"], "expectedGpus": cfg["expected_gpu_count"],
                     "model": cards[0]["model"] if cards else "NVIDIA GPU", "gpus": cards,
                     "startedAt": state["created_at"], "lastSample": state["last_success_at"],
                     "sampleCount": state["sample_count"], "gpuInterval": cfg["gpu_interval"],
                     "problems": gpu_problems, "ledgerId": state["ledger_id"],
                     "users": {key: {**identity(row), "totalSeconds": row["gpu_seconds"],
                                "sharedSeconds": row["shared_gpu_seconds"]} for key, row in state["users"].items()}},
            "collectors": {"gpu": collector_status(state, gpu_problems)},
            "history": samples, "events": sorted(incidents, key=lambda item: item["t"], reverse=True)[:120]}


def export(private_path=PRIVATE_CONFIG, destination=None):
    private_path = Path(private_path).expanduser().resolve()
    if private_path.is_relative_to(REPO):
        raise ValueError("Private configuration must live outside the repository")
    if private_path.stat().st_mode & 0o077:
        raise ValueError("Private configuration must be chmod 600")
    private = read_json(private_path)
    payload = build_payload(private)
    envelope = encrypted(payload, private["password"], base64.b64decode(private["salt"], validate=True))
    destination = destination or REPO / "site/data/snapshot.enc.json"
    atomic_json(destination, envelope)
    return {"output": str(destination), "bytes": Path(destination).stat().st_size,
            "generated_at": payload["generatedAt"], "gpu_count": len(payload["node"]["gpus"]),
            "samples": len(payload["history"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PRIVATE_CONFIG))
    args = parser.parse_args()
    print(json.dumps(export(args.config)))
