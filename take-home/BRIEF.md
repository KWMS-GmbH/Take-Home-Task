# Take-home: Charging Session Service

Thanks for taking the time. This task is close to the work you would do with us:
we run the backend for an EV charging platform. Charging stations ("charge
points") talk to our servers over WebSockets using **OCPP 1.6**. We decide which
drivers may charge where, turn the stations' messages into charging sessions,
and run everything as Docker services.

**Time box: about 6 hours.** Please don't spend much more. An unfinished but
well-reasoned solution beats a polished one that took a weekend. If you run out
of time, follow the priority order at the end and write down what you would do next.

**AI tools are allowed and expected.** Use whatever you normally use. We only ask
for a short note on how you used them (see *Deliverables*). In the follow-up call
we will discuss your code with you and make a small change together, so make sure
you understand and agree with everything you submit.

---

## What to build

Three services, deployed together as a **Docker Swarm stack**:

```text
             browser (us, the testers)
                     │ HTTP
            ┌────────▼─────────┐
            │ frontend (Flask) │── simulated charge point ──┐
            └────────┬─────────┘     (OCPP over WebSocket)  │
                     │ HTTP API                             │
            ┌────────▼─────────────────────────────────────▼┐
            │ backend: OCPP server + HTTP API               │
            └────────┬──────────────────────────────────────┘
                     │
            ┌────────▼─────────┐
            │ db (PostgreSQL)  │
            └──────────────────┘
```

### 1. Backend

- **Accepts WebSocket connections from charge points** at
  `ws://<host>:9000/ocpp/<charge_point_id>`. Charge points request the
  subprotocol `ocpp1.6`.
- **Handles these OCPP messages** (only the fields listed matter, ignore the rest):

  | Action | Relevant request fields | Your response |
  |---|---|---|
  | `BootNotification` | `chargePointVendor`, `chargePointModel` | `{"status": "Accepted", "currentTime": <ISO-8601 UTC>, "interval": 300}` |
  | `Authorize` | `idTag` | `{"idTagInfo": {"status": <see Authorization>}}` |
  | `StatusNotification` | `connectorId`, `status`, `errorCode`, `timestamp` | `{}` |
  | `StartTransaction` | `connectorId`, `idTag`, `meterStart` (Wh), `timestamp` | `{"transactionId": <int>, "idTagInfo": {"status": <see Authorization>}}` |
  | `MeterValues` | `connectorId`, `transactionId`, `meterValue[].timestamp`, `meterValue[].sampledValue[].value` (Wh, as a string) | `{}` |
  | `StopTransaction` | `transactionId`, `meterStop` (Wh), `timestamp`, `reason` (optional) | `{"idTagInfo": {"status": "Accepted"}}` |

- **Stores stations, idTags, permissions and charging sessions** in PostgreSQL.
- **Exposes an HTTP API.** The frontend uses only this API, never the database
  directly. You design the endpoints, but they must at least cover: listing sessions
  per station, one session with its start/stop time, energy in kWh and status, and
  managing idTags, stations and permissions.

### 2. Authorization

Drivers identify themselves with an **idTag** (an RFID card number, for example
`TAG-42`). Whether a driver may charge depends on **the idTag and the station**:

- An idTag can be **permitted at some stations and not at others**.
- The permission for an idTag at a station can be **switched on and off from
  the frontend**, and the change takes effect for the next `Authorize` /
  `StartTransaction` without a restart.
- `idTagInfo.status` must be:
  - `Accepted`: the idTag is known and currently permitted at this station
  - `Blocked`: the idTag is known but not permitted at this station
  - `Invalid`: the idTag is unknown
- Real charge points sometimes start charging even after a rejected `Authorize`.
  OCPP requires `StartTransaction` to always return a `transactionId`, so you
  must handle a `StartTransaction` with a non-permitted idTag sensibly.

You don't need a login for the frontend (but tell us what you'd do in production).

### 3. Frontend (Flask)

The frontend is how **we test and grade your solution**, so make it easy to use.
Plain server-rendered HTML is fine. Design polish is not graded, clarity is.

- **Stations & idTags:** list stations (they appear when they connect; it must
  also be possible to add one by hand, so permissions can be set up in advance),
  create idTags, and switch the permission of each idTag at each station on and off.
- **Simulation:** pick a station, enter an idTag, press *Start*. The frontend then
  acts as that charge point:
  1. it sends `Authorize` for the idTag and shows the result (Accepted / Blocked / Invalid)
  2. **only if Accepted**, it runs the scenarios (see *Tooling we provide*) against the backend
  3. it shows the results per scenario: protocol pass/fail, the messages exchanged
     (collapsible), the scenario's "what to check" lines, and **the sessions the
     scenario produced, taken from your backend API** (status, start, stop,
     duration, energy). We should be able to grade a scenario without
     opening a terminal or a database.
- **Sessions:** a list of sessions per station.

### 4. Docker Swarm deployment

- One stack file (e.g. `stack.yml`) with the three services `backend`,
  `frontend` and `db`, deployable on a fresh single-node swarm:

  ```bash
  docker swarm init
  ./deploy.sh        # or a Makefile: builds the images and runs `docker stack deploy`
  ```

- We expect at least:
  - images built from your own `Dockerfile`s (small, non-root where reasonable)
  - an overlay network. Only what needs to be reachable from outside is published.
  - the DB password provided as a **Docker secret**, not a plain environment variable
  - a persistent volume for the database
  - health checks and a restart policy
  - services that cope with starting in any order (note: `docker stack deploy`
    **ignores `depends_on`**)
- In your README, answer this: **what would happen if we ran
  `docker service scale <stack>_backend=3`**, and what would you change to make
  that work?

---

## The real world is messy

Real charge points don't behave like the spec. Your service should keep working
and keep data **correct** when the following happen. For several of these there
isn't one right answer: pick a behaviour, implement it, and explain why in
your README.

1. **Duplicate messages.** After a network blip a charge point resends a message
   it already sent (same `uniqueId`, same content), for example a `StopTransaction`.
2. **Missing `StopTransaction`.** A charge point reboots in the middle of a
   session (you receive a fresh `BootNotification`) and never sends a
   `StopTransaction` for the open session.
3. **Late messages.** A `StopTransaction` arrives hours late, after the charge
   point reconnects. Its `timestamp` is the real stop time.
4. **Timestamps with offsets.** Some charge points send `2026-10-11T14:05:00+02:00`,
   others `2026-10-11T12:05:00Z`.
5. **Suspicious meter readings.** `meterStop` is smaller than `meterStart`
   (for example after a meter replacement).
6. **Two connections for the same charge point.** A charge point opens a new
   connection before the server has noticed that the old one is dead.
7. **Garbage input.** Invalid JSON, unknown actions, or missing fields must not
   crash the service or affect other charge points.
8. **Not permitted, but charging anyway.** A `StartTransaction` arrives for an
   idTag that is blocked or unknown at this station.
9. **Permission revoked mid-session.** An operator switches off an idTag's permission
   while that driver is charging. You don't have to stop the session. Explain what
   your system does and what it *should* do.

## Tooling we provide

`replay.py` plays the scenario files in `scenarios/` against your backend,
acting as a charge point. It covers cases 1–8. Use it in two ways:

- **From the command line** while developing:

  ```bash
  pip install "websockets>=13"
  python replay.py --url ws://localhost:9000/ocpp \
      --station CP-001 --id-tag TAG-42 --blocked-id-tag TAG-BLOCKED
  ```

  It first sends `Authorize` and stops if the idTag isn't Accepted.

- **As a library from your frontend** (copy it into the frontend image):

  ```python
  import asyncio, replay
  auth = asyncio.run(replay.authorize(ws_url, station, id_tag))
  if auth["status"] == "Accepted":
      results = asyncio.run(replay.run_scenarios(None, ws_url, station=station,
                                                 id_tag=id_tag, blocked_id_tag=other_tag))
  ```

  `results` is a JSON-serialisable list with one entry per scenario (name,
  pass/fail, messages exchanged, check lines). See the docstrings in `replay.py`.
  You may wrap, change or replace it, but explain why if you replace it.

Scenario 08 needs a second idTag that is **not** permitted at the station
(`blocked_id_tag`). Let the tester choose it in the simulation form.

`replay.py` checks the protocol only (framing, `uniqueId`, authorization status).
Whether the resulting sessions are *correct* is what your frontend has to show us.

---

## Deliverables

A Git repository (link or zip) containing:

- **The code**, the `Dockerfile`s, the stack file and the deploy script.
  `docker swarm init && ./deploy.sh` on a clean machine must bring up the
  whole system. Say in the README which URL to open.
- **Automated tests** for the parts you consider most important (authorization
  rules and session correctness, at least). We'd rather see a few meaningful tests
  than many trivial ones.
- **`README.md`** with:
  - how to deploy, how to run the tests, and how to tear everything down
  - your decisions for each of the nine cases above, and why
  - your answer to the `scale backend=3` question
  - known limitations and what you would do next with more time
- **`AI_USAGE.md`** (a few sentences is enough): which tools you used, for what,
  and at least one place where you disagreed with or corrected the AI's output.

Please keep the Git history. Small, honest commits help us understand how you work.

## What we look at

In roughly this order:

1. **Correctness of the data and authorization** under the messy cases above
2. **Docker Swarm setup**: deploys cleanly, secrets, networking, startup ordering, health
3. **Clarity of your reasoning** in the README and in the follow-up call
4. **Robustness**: one bad charge point must not affect others
5. **Tests**: do they cover what actually matters?
6. **Code quality**: readable, reasonably structured, no over-engineering

We do *not* look at OCPP coverage beyond the six messages, or frontend design.

**If you run short of time**, prioritise: a working stack with authorization and
the happy path, then the simulation page, then the messy cases, then everything else.
Tell us what you skipped.

## The follow-up call (about 60 minutes)

- You deploy and walk us through your solution (10 min, no slides needed)
- We discuss your decisions and trade-offs
- We make a small change to your code together, live

Questions before you start? Just email us. Asking is a good sign, not a bad one.
