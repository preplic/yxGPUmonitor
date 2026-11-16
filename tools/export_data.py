#!/usr/bin/python3
"""Export real node-monitor observations as an authenticated encrypted snapshot."""

import argparse
import base64
import csv
import gzip
import importlib
import io
import json
import math
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import sys
import tempfile
import time

from cryptography.exceptions import InvalidTag
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


def snapshot_time(envelope, password):
    salt = base64.b64decode(envelope["salt"], validate=True)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=envelope["iterations"]).derive(password.encode("utf-8"))
    raw = AESGCM(key).decrypt(base64.b64decode(envelope["iv"], validate=True),
                             base64.b64decode(envelope["ciphertext"], validate=True), AAD)
    value = json.loads(gzip.decompress(raw))["generatedAt"]
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid previous snapshot timestamp")
    return value


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


def averaged_metrics(observations, since, until, interval):
    totals = {}
    observations = sorted({t: cards for t, cards in observations if t <= until}.items())
    for index, (t, cards) in enumerate(observations):
        end = observations[index + 1][0] if index + 1 < len(observations) else until
        # Match the collector's gap policy rather than extending stale values across outages.
        if end - t > 3 * interval:
            continue
        seconds = min(end, until) - max(t, since)
        if seconds <= 0:
            continue
        for card in cards:
            values = {"utilization": card.get("utilization"), "memoryUsed": card.get("memory_used")}
            memory_total = card.get("memory_total")
            memory_used = values["memoryUsed"]
            values["memoryUtilization"] = (memory_used / memory_total * 100
                if isinstance(memory_total, (int, float)) and math.isfinite(memory_total) and memory_total > 0
                and isinstance(memory_used, (int, float)) else None)
            item = totals.setdefault(card["uuid"], {"samples": 0, "sums": {}, "seconds": {}})
            valid = False
            for name, value in values.items():
                if (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                        or (name != "memoryUsed" and value > 100)):
                    continue
                valid = True
                item["sums"][name] = item["sums"].get(name, 0) + value * seconds
                item["seconds"][name] = item["seconds"].get(name, 0) + seconds
            item["samples"] += int(valid)
    return {uuid: {**{name: item["sums"].get(name, 0) / item["seconds"][name]
                        if item["seconds"].get(name) else None
                     for name in ("utilization", "memoryUsed", "memoryUtilization")},
                   "metricSamples": item["samples"], "metricCoverage": item["seconds"]}
            for uuid, item in totals.items()}


def history(cfg, state, common, now, metric_start):
    buckets = {}
    first = None
    incidents = []
    observations = []
    metric_since = metric_start - 3 * cfg["gpu_interval"]
    for item in common.read_events(Path(cfg["gpu_root"]) / cfg["host"], min(now - 7 * 86400, metric_since)):
        kind = item["type"]
        if kind in ("start", "stop", "sample_error") and metric_since <= item["time"] <= now:
            observations.append((item["time"], []))
        if kind == "sample" and item.get("ledger_id") == state["ledger_id"] and item["last_success_at"] <= now:
            if item["last_success_at"] >= metric_since:
                observations.append((item["last_success_at"], item.get("current", {}).get("gpus", [])))
            if item["last_success_at"] < now - 7 * 86400:
                continue
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
    observations.append((state["last_success_at"], state.get("current", {}).get("gpus", [])))
    averages = averaged_metrics(observations, metric_start, now, cfg["gpu_interval"])
    return sorted({p["t"]: p for p in points}.values(), key=lambda item: item["t"]), incidents, averages


def gpu_metrics():
    result = subprocess.run(["/usr/bin/nvidia-smi",
        "--query-gpu=index,uuid,name,memory.total,temperature.gpu,power.draw,power.limit",
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
                       "temperature": number(4), "power": number(5), "powerLimit": number(6)})
    return values


def build_payload(private, now=None, previous_generated_at=None):
    now = time.time() if now is None else now
    metric_start = previous_generated_at if previous_generated_at is not None else now - 900
    if not math.isfinite(metric_start) or metric_start >= now:
        raise ValueError("Previous snapshot must precede this update")
    cfg, code = monitor_paths(private)
    sys.path.insert(0, code)
    common = importlib.import_module("monitor_common")
    gpu = importlib.import_module("gpu_usage")
    state = read_json(Path(cfg["gpu_root"]) / cfg["host"] / "status.json")
    gpu_problems = gpu.health(state, now=now)
    samples, incidents, averages = history(cfg, state, common, now, metric_start)
    current = state.get("current", {})
    cards = gpu_metrics()
    for card in cards:
        card.update(averages.get(card["uuid"], {"utilization": None, "memoryUsed": None,
                    "memoryUtilization": None, "metricSamples": 0, "metricCoverage": {}}))
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
                     "metricWindow": {"start": metric_start, "end": now},
                     "problems": gpu_problems, "ledgerId": state["ledger_id"],
                     "users": {key: {**identity(row), "totalSeconds": row["gpu_seconds"],
                                "sharedSeconds": row["shared_gpu_seconds"]} for key, row in state["users"].items()}},
            "collectors": {"gpu": collector_status(state, gpu_problems)},
            "history": samples, "events": sorted(incidents, key=lambda item: item["t"], reverse=True)[:120]}


def export(private_path=PRIVATE_CONFIG, destination=None, previous_snapshot=None):
    private_path = Path(private_path).expanduser().resolve()
    if private_path.is_relative_to(REPO):
        raise ValueError("Private configuration must live outside the repository")
    if private_path.stat().st_mode & 0o077:
        raise ValueError("Private configuration must be chmod 600")
    private = read_json(private_path)
    destination = Path(destination or REPO / "site/data/snapshot.enc.json")
    if previous_snapshot is None and destination.exists():
        previous_snapshot = read_json(destination)
    try:
        previous_time = snapshot_time(previous_snapshot, private["password"]) if previous_snapshot else None
    except InvalidTag:
        print("Previous snapshot cannot be unlocked; starting a new 15-minute averaging window.", file=sys.stderr)
        previous_time = None
    payload = build_payload(private, previous_generated_at=previous_time)
    envelope = encrypted(payload, private["password"], base64.b64decode(private["salt"], validate=True))
    atomic_json(destination, envelope)
    return {"output": str(destination), "bytes": Path(destination).stat().st_size,
            "generated_at": payload["generatedAt"], "gpu_count": len(payload["node"]["gpus"]),
            "metric_window": payload["node"]["metricWindow"],
            "samples": len(payload["history"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PRIVATE_CONFIG))
    args = parser.parse_args()
    print(json.dumps(export(args.config)))
