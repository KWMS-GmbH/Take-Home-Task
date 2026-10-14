#!/usr/bin/env python3
"""Replay OCPP 1.6 scenario files against a charging session service.

Command line:
    python replay.py scenarios/                       # every *.json in the folder
    python replay.py scenarios/01_happy_path.json     # a single scenario
    python replay.py scenarios/ --url ws://localhost:9000/ocpp \\
        --station CP-001 --id-tag TAG-42 --blocked-id-tag TAG-BLOCKED

As a library (e.g. from your Flask frontend):
    import asyncio, replay
    auth = asyncio.run(replay.authorize("ws://backend:9000/ocpp", "CP-001", "TAG-42"))
    # auth == {"status": "Accepted", "error": None, "frames": [...]}
    results = asyncio.run(replay.run_scenarios(["scenarios/"], "ws://backend:9000/ocpp",
                                               station="CP-001", id_tag="TAG-42",
                                               blocked_id_tag="TAG-BLOCKED"))
    # results: one dict per scenario, see run_scenario() for the shape. JSON-serialisable.

Checks protocol behaviour only (response framing, uniqueId echo, connection
stays usable, idTag authorization result). Data correctness is up to you: each
scenario lists what to verify afterwards in its "check" lines.

Scenario file format (JSON):
    {
      "name": "...", "description": "...", "check": ["what to verify ..."],
      "steps": [
        {"conn": "a", "open": "$station"},
        {"conn": "a", "call": "StartTransaction", "payload": {...},
         "save": {"tx": "transactionId"}, "expect_status": "Accepted"},
        {"conn": "a", "call": "StopTransaction", "id": "fixed-id",
         "payload": {"transactionId": "$tx", "timestamp": "$ts(-5m,+02:00)"}},
        {"conn": "a", "raw": "not json", "expect": "any"},
        {"conn": "a", "close": true},
        {"sleep": 0.5}
      ]
    }

"expect_status" checks idTagInfo.status in the response ("!Accepted" = anything else).

Placeholders inside strings:
    $station            the charge point id under test (--station)
    $idtag              an idTag that is permitted at that station (--id-tag)
    $blocked_idtag      an idTag that is NOT permitted at that station (--blocked-id-tag)
    $name               a value saved earlier via "save" (whole string only)
    $ts(-90m)           now minus 90 minutes, UTC ("Z")
    $ts(-2h,+02:00)     now minus 2 hours, rendered with a +02:00 offset
    $run                a short id unique to this scenario run
"""

import argparse
import asyncio
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import websockets
except ImportError:
    sys.exit('Missing dependency: pip install "websockets>=13"')

CALL, CALLRESULT, CALLERROR = 2, 3, 4
TS_RE = re.compile(r"\$ts\(([+-]\d+)([smhd])(?:,([+-]\d{2}:\d{2}))?\)")
UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
SCENARIO_DIR = Path(__file__).parent / "scenarios"


class Result:
    """Collects everything that happened in one scenario as a list of events."""

    def __init__(self):
        self.fails = 0
        self.warns = 0
        self.events = []

    def log(self, kind, conn, text):
        self.events.append({"kind": kind, "conn": conn, "text": text})

    def ok(self, msg):
        self.log("pass", None, msg)

    def warn(self, msg):
        self.warns += 1
        self.log("warn", None, msg)

    def fail(self, msg):
        self.fails += 1
        self.log("fail", None, msg)


def render_ts(match):
    amount, unit, offset = int(match.group(1)), match.group(2), match.group(3)
    t = datetime.now(timezone.utc) + timedelta(**{UNITS[unit]: amount})
    t = t.replace(microsecond=0)
    if not offset:
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")
    sign = 1 if offset[0] == "+" else -1
    hours, minutes = map(int, offset[1:].split(":"))
    tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
    return t.astimezone(tz).isoformat()


def substitute(value, saved, params):
    if isinstance(value, dict):
        return {k: substitute(v, saved, params) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, saved, params) for v in value]
    if isinstance(value, str):
        if value.startswith("$") and value[1:] in saved:
            return saved[value[1:]]
        # longest names first, so "$blocked_idtag" isn't partially replaced
        for name in sorted(params, key=len, reverse=True):
            value = value.replace(f"${name}", str(params[name]))
        return TS_RE.sub(render_ts, value)
    return value


async def recv_json(ws, timeout):
    raw = await asyncio.wait_for(ws.recv(), timeout)
    return raw, json.loads(raw)


def check_status(step, action, payload, res):
    """Check idTagInfo.status against "expect_status" ("Accepted" or "!Accepted")."""
    expected = step.get("expect_status")
    if expected is None:
        return True
    status = (payload.get("idTagInfo") or {}).get("status")
    if expected.startswith("!"):
        good = status is not None and status != expected[1:]
    else:
        good = status == expected
    if not good:
        res.fail(f"{action}: idTagInfo.status is {status!r}, expected {expected}")
    return good


async def do_call(step, conns, saved, params, res, timeout):
    ws = conns[step["conn"]]
    action = step["call"]
    unique_id = substitute(step.get("id"), saved, params) or uuid.uuid4().hex[:12]
    payload = substitute(step.get("payload", {}), saved, params)
    frame = [CALL, unique_id, action, payload]
    res.log("send", step["conn"], json.dumps(frame))
    await ws.send(json.dumps(frame))

    try:
        raw, msg = await recv_json(ws, timeout)
    except asyncio.TimeoutError:
        res.fail(f"no response to {action} within {timeout}s")
        return None
    except json.JSONDecodeError:
        res.fail(f"response is not valid JSON: {raw!r}")
        return None
    except websockets.ConnectionClosed as e:
        res.fail(f"connection closed while waiting for {action} response: {e}")
        return None
    res.log("recv", step["conn"], raw)

    expect = step.get("expect", "callresult")
    if not isinstance(msg, list) or len(msg) < 3 or msg[0] not in (CALLRESULT, CALLERROR):
        res.fail("response is not a CALLRESULT or CALLERROR array")
        return None
    if msg[1] != unique_id:
        res.fail(f"response uniqueId {msg[1]!r} does not match request {unique_id!r}")
        return None

    if msg[0] == CALLRESULT:
        if len(msg) != 3 or not isinstance(msg[2], dict):
            res.fail("CALLRESULT must be [3, uniqueId, {payload}]")
            return None
        if expect == "callerror":
            res.warn(f"{action}: got CALLRESULT, a CALLERROR was expected here")
            return msg[2]
        for name, field in step.get("save", {}).items():
            if field not in msg[2]:
                res.fail(f"{action} response has no '{field}'")
                return None
            saved[name] = msg[2][field]
        for field in step.get("require", []):
            if field not in msg[2]:
                res.fail(f"{action} response is missing required field '{field}'")
                return None
        if check_status(step, action, msg[2], res):
            status = (msg[2].get("idTagInfo") or {}).get("status")
            res.ok(f"{action} -> CALLRESULT" + (f" ({status})" if status else ""))
        return msg[2]

    if len(msg) != 5:
        res.fail("CALLERROR must be [4, uniqueId, errorCode, description, {details}]")
        return None
    if expect == "callresult":
        res.fail(f"{action}: got CALLERROR {msg[2]}, a CALLRESULT was expected")
        return None
    res.ok(f"{action} -> CALLERROR {msg[2]}")
    return None


async def do_raw(step, conns, res, timeout):
    ws = conns[step["conn"]]
    res.log("send", step["conn"], f"(raw) {step['raw']!r}")
    await ws.send(step["raw"])
    try:
        # Silence is a valid answer to garbage, so don't wait the full timeout.
        raw, _ = await recv_json(ws, min(timeout, 2.0))
        res.log("recv", step["conn"], raw)
        res.ok("server answered garbage without crashing")
    except asyncio.TimeoutError:
        res.ok("server ignored garbage (no response)")
    except json.JSONDecodeError:
        res.warn("server answered garbage with a non-JSON frame")
    except websockets.ConnectionClosed:
        res.warn("server closed the connection on garbage input")


async def do_open(step, conns, url, params, res):
    cp_id = substitute(step["open"], {}, params)
    target = f"{url.rstrip('/')}/{cp_id}"
    res.log("info", step["conn"], f"open {target}")
    try:
        ws = await websockets.connect(target, subprotocols=["ocpp1.6"], open_timeout=5)
    except Exception as e:  # noqa: BLE001 - report any connect failure
        res.fail(f"could not connect: {e}")
        return False
    if ws.subprotocol != "ocpp1.6":
        res.warn(f"server did not accept subprotocol ocpp1.6 (got {ws.subprotocol!r})")
    conns[step["conn"]] = ws
    return True


def make_params(station, id_tag, blocked_id_tag):
    return {"station": station, "idtag": id_tag, "blocked_idtag": blocked_id_tag,
            "run": uuid.uuid4().hex[:6]}


async def run_scenario(path, url, station="CP-001", id_tag="TAG-42",
                       blocked_id_tag="TAG-BLOCKED", timeout=5.0):
    """Run one scenario file. Returns a JSON-serialisable dict:

    {"file", "name", "description", "check": [str], "charge_points": [str],
     "fails": int, "warns": int, "passed": bool,
     "events": [{"kind": "send"|"recv"|"info"|"pass"|"warn"|"fail", "conn", "text"}]}
    """
    spec = json.loads(Path(path).read_text())
    params = make_params(station, id_tag, blocked_id_tag)
    res, conns, saved = Result(), {}, {}
    try:
        for step in spec["steps"]:
            if "sleep" in step:
                await asyncio.sleep(step["sleep"])
            elif "open" in step:
                if not await do_open(step, conns, url, params, res):
                    break
            elif step.get("close"):
                res.log("info", step["conn"], "close")
                await conns.pop(step["conn"]).close()
            elif "conn" in step and step["conn"] not in conns:
                res.fail(f"connection '{step['conn']}' is not open")
                break
            elif "raw" in step:
                await do_raw(step, conns, res, timeout)
            elif "call" in step:
                await do_call(step, conns, saved, params, res, timeout)
            if "conn" in step and step["conn"] in conns and conns[step["conn"]].close_code is not None:
                if not step.get("may_close"):
                    res.warn(f"server closed connection '{step['conn']}'")
                conns.pop(step["conn"])
    finally:
        for ws in conns.values():
            await ws.close()

    return {
        "file": Path(path).name,
        "name": spec["name"],
        "description": spec.get("description", ""),
        "check": [substitute(c, {}, params) for c in spec.get("check", [])],
        "charge_points": sorted({substitute(s["open"], {}, params) for s in spec["steps"] if "open" in s}),
        "fails": res.fails,
        "warns": res.warns,
        "passed": res.fails == 0,
        "events": res.events,
    }


async def run_scenarios(paths, url, station="CP-001", id_tag="TAG-42",
                        blocked_id_tag="TAG-BLOCKED", timeout=5.0):
    """Run scenario files and/or folders (default: the bundled scenarios/ folder) in order."""
    files = []
    for p in map(Path, paths or [SCENARIO_DIR]):
        files += sorted(p.glob("*.json")) if p.is_dir() else [p]
    return [await run_scenario(f, url, station, id_tag, blocked_id_tag, timeout) for f in files]


async def authorize(url, station, id_tag, timeout=5.0):
    """Connect as `station`, send BootNotification + Authorize(id_tag), return the outcome.

    Returns {"status": "Accepted" | "Blocked" | "Invalid" | ... | None, "error": str | None,
             "frames": [events]}.
    """
    res = Result()
    conns = {}
    params = make_params(station, id_tag, "")
    status, error = None, None
    try:
        if not await do_open({"conn": "a", "open": "$station"}, conns, url, params, res):
            return {"status": None, "error": "could not connect", "frames": res.events}
        await do_call({"conn": "a", "call": "BootNotification",
                       "payload": {"chargePointVendor": "ACME", "chargePointModel": "DC-150"}},
                      conns, {}, params, res, timeout)
        payload = await do_call({"conn": "a", "call": "Authorize", "payload": {"idTag": "$idtag"},
                                 "require": ["idTagInfo"]}, conns, {}, params, res, timeout)
        if payload is not None:
            status = (payload.get("idTagInfo") or {}).get("status")
        if res.fails:
            error = next(e["text"] for e in res.events if e["kind"] == "fail")
    finally:
        for ws in conns.values():
            await ws.close()
    return {"status": status, "error": error, "frames": res.events}


# --- command line -------------------------------------------------------------

COLORS = {"pass": "\033[32mPASS\033[0m", "warn": "\033[33mWARN\033[0m", "fail": "\033[31mFAIL\033[0m"}


def print_result(r):
    print(f"\n=== {r['name']} ({r['file']})")
    print(f"    {r['description']}")
    for e in r["events"]:
        if e["kind"] in COLORS:
            print(f"    {COLORS[e['kind']]} {e['text']}")
        else:
            arrow = {"send": "->", "recv": "<-"}.get(e["kind"], "  ")
            print(f"  [{e['conn']}] {arrow} {e['text']}")
    if r["check"]:
        print(f"  Now check your API for charge point(s) {', '.join(r['charge_points'])}:")
        for line in r["check"]:
            print(f"    - {line}")


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="scenario files or folders (default: bundled scenarios/)")
    parser.add_argument("--url", default="ws://localhost:9000/ocpp", help="base URL, charge point id is appended")
    parser.add_argument("--station", default="CP-001", help="charge point id under test")
    parser.add_argument("--id-tag", default="TAG-42", help="idTag that is permitted at the station")
    parser.add_argument("--blocked-id-tag", default="TAG-BLOCKED", help="idTag that is NOT permitted there")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds to wait for each response")
    args = parser.parse_args()

    auth = await authorize(args.url, args.station, args.id_tag, args.timeout)
    print(f"Authorize {args.id_tag} at {args.station}: {auth['status'] or auth['error']}")
    if auth["status"] != "Accepted":
        print("idTag is not authorized at this station - enable it in your frontend first.")
        sys.exit(2)

    results = await run_scenarios(args.paths, args.url, args.station, args.id_tag,
                                  args.blocked_id_tag, args.timeout)
    for r in results:
        print_result(r)
    fails = sum(r["fails"] for r in results)
    warns = sum(r["warns"] for r in results)
    print(f"\n{len(results)} scenario(s): {fails} protocol failure(s), {warns} warning(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
