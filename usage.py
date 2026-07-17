#!/usr/bin/env python3
"""
usage.py — real-time model-routing / usage timeline for Claude Code sessions.

Watches every session + subagent transcript under ~/.claude/projects and prints,
per assistant turn, which model actually served it (server-echoed `model` field),
flagging refusal-fallback switches (Fable -> Opus) and tallying per-model usage.

This is a READ-ONLY log viewer. It reports what already happened; it does not
change, bypass, or influence any request or the safeguard that triggers a switch.
Purpose: understand which model you are actually billed for, turn by turn.

Note on fallback detection: Claude Code sends the switch decision to the API via
`x-cc-fallback-*` / `x-is-refusal-fallback` HTTP headers, which are NOT written to
the transcript. This tool detects the same turns from their in-log footprint:
a served-model change within a thread, corroborated by
`message.diagnostics.cache_miss_reason.type == "model_changed"`.

Usage:
  python3 usage.py                 # live tail of the last 24h (Ctrl-C to stop)
  python3 usage.py --all           # scan all history instead of the 24h default
  python3 usage.py --once          # print recent timeline + summary, exit
  python3 usage.py --fallbacks     # only show switches/fallbacks + user turns
  python3 usage.py --filter scry   # only sources matching substring
  python3 usage.py --history 60    # past turns to show on start
  python3 usage.py --since 7d      # only turns from the last 7 days (also: 12h, 30m, ISO date)
  python3 usage.py --today         # shortcut for --since today (local midnight)
  python3 usage.py --daily         # per-day rollup table (implies --once)
  python3 usage.py --no-color

Costs are estimated API-equivalent $ at current list rates (input, output,
cache read at 0.1x input, cache write at 1.25x input) — what the usage would
bill on the API, not what a subscription actually charges.
"""
import os, sys, json, time, glob, argparse, re, shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT_DEFAULT = os.path.expanduser("~/.claude/projects")
DESC_W = 40

# named topic filters for --topic (case-insensitive regex over turn content)
TOPIC_PRESETS = {
    "security": r"\b(xss|csrf|cve|vuln|exploit|payload|rce|sql ?inj|injection|"
                r"privilege|escalat|malware|cyber|attacker|sanitiz|shellcode|"
                r"poc|nightmare|rogueplanet|legacyhive|regloadkey|registry hive)\b",
}

MODEL_SHORT = {
    "claude-fable-5": "FABLE", "claude-mythos-5": "MYTHOS",
    "claude-opus-4-8": "OPUS", "claude-sonnet-5": "SONNET",
}

# transcript text is untrusted: strip C0/C1 control bytes so it can't inject
# terminal escapes (title changes, cursor moves, OSC 52 clipboard writes)
CTRL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
def scrub(s):
    return CTRL_CHARS.sub("", s)

# records that parsed as JSON but blew up in make_event (schema drift)
SKIPPED = {"n": 0}

# $/MTok (input, output) at current API list rates. Cache read bills at 0.1x
# input, cache write at 1.25x input. Matched by key prefix against shortnames
# (OPUS-4-7, SONNET-4, 3-5-SONN all match). Unknown models cost $0.00.
# Note: pre-4.5 Opus was $15/$75 — historical OPUS-4-1 turns underestimate.
PRICES = {
    "FABLE": (10.0, 50.0), "MYTHOS": (10.0, 50.0),
    "OPUS": (5.0, 25.0), "SONNET": (3.0, 15.0), "HAIKU": (1.0, 5.0),
}

def event_cost(short, inp, out, cache_r, cache_c):
    for key, (pi, po) in PRICES.items():
        if key[:4] in short:
            return (inp * pi + out * po + cache_r * pi * 0.1 + cache_c * pi * 1.25) / 1e6
    return 0.0


def parse_since(s):
    """'7d' / '12h' / '30m', 'today', or an ISO date -> aware local datetime."""
    if not s:
        return None
    s = s.strip().lower()
    now = datetime.now().astimezone()
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([dhm])", s)
    if m:
        secs = float(m.group(1)) * {"d": 86400, "h": 3600, "m": 60}[m.group(2)]
        return now - timedelta(seconds=secs)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"invalid --since {s!r}: use 7d, 12h, 30m, 'today', or an ISO date")
    return dt.astimezone()
def model_short(m):
    if not m:
        return None
    if m in MODEL_SHORT:
        return MODEL_SHORT[m]
    if m.startswith("claude-haiku"):
        return "HAIKU"
    if m == "<synthetic>":
        return None
    return m.replace("claude-", "").upper()[:8]


class C:
    enabled = True
    def _w(code):
        return lambda s: (f"\033[{code}m{s}\033[0m" if C.enabled else s)
    dim = _w("2"); bold = _w("1"); green = _w("32"); yellow = _w("33")
    red = _w("1;31"); cyan = _w("36"); blue = _w("34"); grn_b = _w("1;32")

def model_color(short):
    return {"FABLE": C.green, "MYTHOS": C.green, "OPUS": C.yellow,
            "SONNET": C.cyan, "HAIKU": C.blue}.get(short, lambda s: s)


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def clean_user_text(msg):
    c = msg.get("content"); parts = []
    if isinstance(c, str):
        parts = [c]
    elif isinstance(c, list):
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
            elif isinstance(p, dict) and p.get("type") == "tool_result":
                return None
    txt = " ".join(parts).strip()
    if not txt or txt.startswith("<system-reminder>") or "<task-notification>" in txt:
        return None
    wrap = "The user sent a new message while you were working:"
    if wrap in txt:
        txt = txt.split(wrap, 1)[1]
    txt = txt.split("This is how Claude Code surfaces", 1)[0]
    return scrub(" ".join(txt.split())) or None


def tool_desc(part):
    inp = part.get("input", {}) or {}
    for k in ("query", "command", "pattern", "prompt", "description",
              "url", "file_path", "path"):
        if k in inp and inp[k]:
            return f"{part.get('name')}({scrub(str(inp[k])[:44].splitlines()[0])})"
    return part.get("name") or "tool"


def make_event(o):
    m = o.get("message")
    if not isinstance(m, dict):
        return None
    ts = parse_ts(o.get("timestamp", ""))
    cwd = o.get("cwd", "")
    project = os.path.basename(cwd) if cwd else (o.get("sessionId") or "")[:6]
    agent = o.get("attributionAgent"); agent_id = o.get("agentId")
    label = (agent or "sub") + "#" + str(agent_id)[-4:] if agent_id else "main"
    source = scrub(f"{project}:{label}")
    thread = (o.get("sessionId"), agent_id or "main")

    role = m.get("role")
    if role == "user":
        txt = clean_user_text(m)
        if not txt:
            return None
        return dict(kind="U", ts=ts, source=source, thread=thread,
                    desc="user: " + txt[:80])
    if role != "assistant":
        return None
    short = model_short(m.get("model"))
    if not short:
        return None
    content = m.get("content") or []
    tools = [tool_desc(p) for p in content
             if isinstance(p, dict) and p.get("type") == "tool_use"]
    text = "".join(p.get("text", "") for p in content
                   if isinstance(p, dict) and p.get("type") == "text").strip()
    if tools:
        desc = "-> " + ", ".join(tools)
    elif text:
        desc = scrub(text[:DESC_W].replace("\n", " "))
    else:
        desc = f"({m.get('stop_reason') or '...'})"
    u = m.get("usage")
    u = u if isinstance(u, dict) else {}
    diag = m.get("diagnostics")
    diag = diag if isinstance(diag, dict) else {}
    cmr = diag.get("cache_miss_reason")
    cmr = cmr if isinstance(cmr, dict) else {}
    blob = (text + " " + " ".join(tools)).lower()
    is_fork = bool(o.get("isSidechain")) or agent == "fork" or bool(agent_id)
    out_t = u.get("output_tokens", 0); in_t = u.get("input_tokens", 0)
    cr = u.get("cache_read_input_tokens", 0); cc = u.get("cache_creation_input_tokens", 0)
    return dict(kind="A", ts=ts, source=source, thread=thread, model=short,
                desc=desc, sr=m.get("stop_reason"), blob=blob,
                is_fork=is_fork, attr=(agent or "main"), msg_id=m.get("id"),
                out=out_t, inp=in_t, cache_r=cr, cache_c=cc,
                cost=event_cost(short, in_t, out_t, cr, cc),
                model_changed=(cmr.get("type") == "model_changed"))


class Tally:
    """Per-model usage accumulator + switch counters."""
    def __init__(self):
        self.m = defaultdict(lambda: dict(turns=0, out=0, inp=0, cr=0, cc=0, cost=0.0))
        self.fallbacks = 0        # Fable -> other, corroborated by model_changed
        self.away = 0             # Fable -> other without the diagnostic (/model etc.)
        self.reverts = 0          # back-to-Fable switches

    def add(self, ev):
        d = self.m[ev["model"]]
        d["turns"] += 1; d["out"] += ev["out"]; d["inp"] += ev["inp"]
        d["cr"] += ev["cache_r"]; d["cc"] += ev["cache_c"]
        d["cost"] += ev.get("cost", 0.0)

    def count(self, cls):
        if cls == "fallback":
            self.fallbacks += 1
        elif cls == "away":
            self.away += 1
        elif cls == "revert":
            self.reverts += 1

    def summary(self, label="this view"):
        lines = [C.bold(f"── per-model usage ({label}) ──")]
        for mdl, d in sorted(self.m.items(), key=lambda kv: -kv[1]["cost"]):
            mc = model_color(mdl)
            lines.append(
                f"  {mc(mdl.ljust(8))} turns={d['turns']:<4} "
                f"out={d['out']:>8,}  in={d['inp']:>8,}  "
                f"cache_read={d['cr']:>9,}  cache_write={d['cc']:>7,}  "
                f"cost=${d['cost']:>9,.2f}")
        total = sum(d["cost"] for d in self.m.values())
        lines.append(C.bold(f"  est. API-equivalent cost: ${total:,.2f}") +
                     C.dim("  (list rates; cache read 0.1x, write 1.25x input)"))
        lines.append(
            f"  {C.red('refusal fallbacks (Fable→other, corroborated)')}: {self.fallbacks}"
            f"   |   uncorroborated Fable→other: {self.away}"
            f"   |   reverts to Fable: {self.reverts}")
        return "\n".join(lines)


def annotate(prev, cur, model_changed):
    """Classify the prev->cur transition. Returns (annotation, cls) with cls in
    None | 'fallback' | 'away' | 'revert' | 'switch'. A Fable->other change only
    counts as a refusal fallback when corroborated by the model_changed
    cache-miss diagnostic (per the detection contract in the module docstring);
    otherwise it's reported as an ordinary switch."""
    tag = C.dim(" [model_changed]") if model_changed else ""
    if prev is None or prev == cur:
        return (tag if model_changed else "", None)
    if prev == "FABLE" and cur != "FABLE":
        if model_changed:
            return (C.red(f"  <== FALLBACK {prev}->{cur} "
                          f"(refusal-fallback footprint)") + tag, "fallback")
        return (C.yellow(f"  <== switched {prev}->{cur} "
                         f"(no model_changed diagnostic)"), "away")
    if cur == "FABLE" and prev != "FABLE":
        return (C.grn_b(f"  <== reverted {prev}->{cur}") + tag, "revert")
    return (C.yellow(f"  <== switched {prev}->{cur}") + tag, "switch")


def render(ev):
    t = ev["ts"].strftime("%H:%M:%S") if ev["ts"] else "--:--:--"
    src = ev["source"][:20].ljust(20)
    if ev["kind"] == "U":
        return f"{C.dim(t)}  {C.dim(src)}  {C.dim(ev['desc'])}"
    desc = ev["desc"]
    if len(desc) >= DESC_W:
        desc = desc[:DESC_W - 1] + "…"; lead = " "
    else:
        lead = " " + "." * (DESC_W - len(desc) - 1) + " "
    mc = model_color(ev["model"])
    # usage shown once per message; continuation records of the same message
    # (ev["dup"]) leave the columns blank instead of repeating the snapshot
    toks = C.dim(f"{ev['out']:>5} out") if ev.get("out") and not ev.get("dup") else " " * 9
    cost = C.dim(f" ${ev['cost']:8.4f}") if ev.get("cost") and not ev.get("dup") else " " * 10
    return f"{t}  {C.dim(src)}  {desc}{lead}{mc(ev['model'].ljust(8))}  {toks}{cost}{ev.get('ann','')}"


def source_ok(ev, filt):
    """--filter match on project/source or session id."""
    return not filt or filt in ev["source"] or filt in str(ev["thread"][0])


class Pin:
    """Freeze the summary at the top of the terminal during live mode using an
    ANSI scroll region (DECSTBM): the timeline scrolls below the pinned block.
    Caveat: lines scrolled inside a region don't enter terminal scrollback."""
    def __init__(self):
        self.active = False
        self.n = 0
        self.rows = 0

    def _region(self, n):
        self.rows = shutil.get_terminal_size().lines
        self.n = n
        # reserve rows 1..n for the summary; scroll region is n+1..bottom
        sys.stdout.write(f"\x1b[{n + 1};{self.rows}r\x1b[{self.rows};1H")

    def start(self, lines):
        sys.stdout.write("\x1b[2J\x1b[H")   # clear viewport (scrollback survives)
        self._region(len(lines))
        # begin printing just under the header; output fills downward and only
        # starts scrolling once the region is full (no dead gap at startup)
        sys.stdout.write(f"\x1b[{len(lines) + 1};1H")
        self.active = True
        self.draw(lines)

    def draw(self, lines):
        if not self.active:
            return
        if len(lines) != self.n or shutil.get_terminal_size().lines != self.rows:
            self._region(len(lines))    # summary grew or terminal resized
        out = ["\x1b7"]                 # save cursor
        for i, ln in enumerate(lines):
            out.append(f"\x1b[{i + 1};1H\x1b[2K{ln}")
        out.append("\x1b8")             # restore cursor
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def stop(self):
        if self.active:
            sys.stdout.write("\x1b[r")  # reset scroll region
            sys.stdout.flush()
            self.active = False


def collect(offsets, root, since=None):
    events = []
    for path in sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)):
        try:
            st = os.stat(path)
        except OSError:
            continue
        size = st.st_size
        # a file not written since the cutoff cannot contain in-window events
        if since is not None and st.st_mtime < since.timestamp():
            continue
        off = offsets.get(path, 0)
        if off > size:
            # file shrank (rewrite/rotation): skip to the new EOF rather than
            # re-ingest from 0 and double-count into the persistent tallies
            offsets[path] = off = size
        if off == size:
            continue
        try:
            with open(path, "rb") as f:
                f.seek(off); data = f.read(size - off)
        except OSError:
            continue
        nl = data.rfind(b"\n")
        if nl == -1:
            continue
        offsets[path] = off + nl + 1
        for raw in data[:nl + 1].split(b"\n"):
            raw = raw.strip()
            if not raw:
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            try:
                ev = make_event(o)
            except Exception:
                # valid JSON but unexpected shape (schema drift) — skip the
                # record, keep the monitor alive, report the count in summaries
                SKIPPED["n"] += 1
                continue
            if not ev:
                continue
            if since is not None and (ev["ts"] is None or ev["ts"] < since):
                continue
            events.append(ev)
    events.sort(key=lambda e: (e["ts"] or datetime.min.replace(tzinfo=timezone.utc)))
    return events


def event_matches(ev, flt):
    """Display filter. Assistant turns tested against model/forks/topic;
    user turns shown only when no audit filter is active."""
    if ev["kind"] != "A":
        return flt.get("trace") or not (flt["model"] or flt["forks_only"] or flt["topic_re"])
    if flt["model"] and ev["model"] != flt["model"]:
        return False
    if flt["forks_only"] and not ev.get("is_fork"):
        return False
    if flt["topic_re"] and not flt["topic_re"].search(ev.get("blob", "")):
        return False
    return True


def emit(ev, last_model, last_msg, tally, filtered, flt, do_print, src_filt=""):
    """Annotate over ALL turns — even ones excluded by --filter — so per-thread
    switch detection never sees gaps. Tally turns matching the source filter;
    print + count into `filtered` only if the display filters also pass.
    One assistant message is split across multiple transcript records, each
    repeating the same usage snapshot — tally only the first (dedupe by
    message id), or token/cost totals overcount severalfold."""
    src_ok = source_ok(ev, src_filt)
    cls = None
    if ev["kind"] == "A":
        prev = last_model.get(ev["thread"])
        ev["ann"], cls = annotate(prev, ev["model"], ev["model_changed"])
        last_model[ev["thread"]] = ev["model"]
        mid = ev.get("msg_id")
        ev["dup"] = bool(mid) and last_msg.get(ev["thread"]) == mid
        last_msg[ev["thread"]] = mid
        if src_ok and not ev["dup"]:
            tally.add(ev)
            tally.count(cls)
        if flt.get("trace"):
            chg = " [CHG]" if ev.get("model_changed") else ""
            ev["ann"] = C.dim(f"  sr={ev.get('sr') or '-'}{chg}") + ev["ann"]
    else:
        ev["ann"] = ""
    if not do_print or not src_ok or not event_matches(ev, flt):
        return cls
    if flt["fallbacks_only"] and ev["kind"] == "A" and "==" not in ev["ann"]:
        return cls
    if ev["kind"] == "A" and not ev["dup"]:
        filtered.add(ev)
        filtered.count(cls)
    print(render(ev))
    return cls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--filter", default="", help="substring match on source/session")
    ap.add_argument("--history", type=int, default=40)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--since", default=None,
                    help="only turns after: 7d, 12h, 30m, 'today', or an ISO date "
                         "(default: 24h, except --audit which scans everything)")
    ap.add_argument("--all", action="store_true",
                    help="scan all history (overrides --since / the 24h default)")
    ap.add_argument("--today", action="store_true", help="shortcut for --since today")
    ap.add_argument("--daily", action="store_true",
                    help="per-day rollup table: turns, tokens, cost, switches (implies --once)")
    ap.add_argument("--fallbacks", action="store_true",
                    help="only show model switches + user turns")
    ap.add_argument("--forks-only", action="store_true",
                    help="only fork/subagent turns")
    ap.add_argument("--model", default="",
                    help="only this served model (FABLE/OPUS/SONNET/HAIKU)")
    ap.add_argument("--topic", default="",
                    help="regex over turn content; preset name 'security' available")
    ap.add_argument("--audit", action="store_true",
                    help="shortcut: --forks-only --model FABLE --topic security --once")
    ap.add_argument("--trace", action="store_true",
                    help="per-turn detail: stop_reason + [CHG] flag + user turns "
                         "(best with --filter <session/agent> to scope one thread)")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    if args.interval <= 0:
        ap.error("--interval must be > 0")
    if args.today:
        args.since = "today"
    if args.all:
        args.since = ""
    elif args.since is None:
        args.since = "" if args.audit else "24h"
    try:
        since = parse_since(args.since)
    except ValueError as e:
        ap.error(str(e))
    if args.daily:
        args.once = True
    if args.audit:
        args.forks_only = True
        args.model = args.model or "FABLE"
        args.topic = args.topic or "security"
        args.once = True
        args.history = max(args.history, 100000)
    if args.no_color or not sys.stdout.isatty():
        C.enabled = False

    topic_pat = TOPIC_PRESETS.get(args.topic.lower(), args.topic) if args.topic else ""
    try:
        topic_re = re.compile(topic_pat, re.I) if topic_pat else None
    except re.error as e:
        ap.error(f"invalid --topic regex {topic_pat!r}: {e}")
    flt = dict(model=args.model.upper(), forks_only=args.forks_only,
               topic_re=topic_re,
               fallbacks_only=args.fallbacks, trace=args.trace)
    active = any([flt["model"], flt["forks_only"], flt["topic_re"]])

    offsets, last_model, last_msg, tally, filtered = {}, {}, {}, Tally(), Tally()
    print(C.bold("Claude Code model-routing timeline") +
          C.dim(f"   root={args.root}  tz={datetime.now().astimezone().tzname()}"))
    if active:
        print(C.dim(f"  filters: model={flt['model'] or 'any'}  "
                    f"forks_only={flt['forks_only']}  topic={args.topic or 'any'}"))
    if since:
        print(C.dim(f"  since: {since:%Y-%m-%d %H:%M %Z}"))
    if not args.daily:
        print(C.dim("time      source                request".ljust(73) +
                    "model         out      cost    switch"))
        print(C.dim("-" * 110))

    # History: annotate ALL in order (so switch detection is correct), print the
    # tail window of source-matching turns that pass the filters.
    daily = defaultdict(lambda: dict(turns=0, out=0, cost=0.0, fb=0, away=0,
                                     rev=0, models=defaultdict(int))) if args.daily else None
    history = collect(offsets, args.root, since)
    match_i = [i for i, ev in enumerate(history) if source_ok(ev, args.filter)]
    cut = match_i[-args.history] if len(match_i) > args.history else 0
    for i, ev in enumerate(history):
        cls = emit(ev, last_model, last_msg, tally, filtered, flt,
                   do_print=(not args.daily and i >= cut), src_filt=args.filter)
        if daily is not None and ev["kind"] == "A" and ev["ts"] \
                and not ev.get("dup") and source_ok(ev, args.filter):
            d = daily[ev["ts"].date()]
            d["turns"] += 1; d["out"] += ev["out"]; d["cost"] += ev["cost"]
            d["models"][ev["model"]] += 1
            if cls == "fallback":
                d["fb"] += 1
            elif cls == "away":
                d["away"] += 1
            elif cls == "revert":
                d["rev"] += 1
    del history, match_i   # can be huge; only tallies/offsets are needed live

    if daily is not None:
        print(C.bold("── daily rollup ──"))
        print(C.dim("date        turns    out tokens     cost      fbk unc rev  models (turns)"))
        for day in sorted(daily):
            d = daily[day]
            models = " ".join(f"{m}:{n}" for m, n in
                              sorted(d["models"].items(), key=lambda kv: -kv[1]))[:44]
            fbk = C.red(f"{d['fb']:>3}") if d["fb"] else f"{d['fb']:>3}"
            print(f"{day}  {d['turns']:<7,} {d['out']:>12,}  ${d['cost']:>8,.2f}  "
                  f"{fbk} {d['away']:>3} {d['rev']:>3}  {C.dim(models)}")

    parts = [f"since {args.since}" if since else "all history"]
    if args.filter:
        parts.append(f"filter={args.filter}")
    tally_label = " · ".join(parts)
    print()
    if active:
        n = sum(d["turns"] for d in filtered.m.values())
        print(C.bold(f"── filtered matches: {n} assistant turns ──"))
        print(filtered.summary("displayed turns"))
        if flt["model"] and n == 0 and tally.m:
            print(C.yellow(f"  warning: --model {flt['model']!r} matched no turns; "
                           f"models seen: {', '.join(sorted(tally.m))}"))
    else:
        print(tally.summary(tally_label))
    if SKIPPED["n"]:
        print(C.dim(f"  ({SKIPPED['n']} unparseable transcript records skipped)"))

    if args.once:
        return
    print(C.bold(C.green("\n---- live (Ctrl-C to stop) ----")))
    live_tally = filtered if active else tally
    live_label = ("displayed turns" if active else tally_label) + " · live"

    def pin_lines():
        return live_tally.summary(live_label).split("\n") + [C.dim("-" * 110)]

    pin = Pin() if sys.stdout.isatty() else None
    if pin:
        pin.start(pin_lines())
    try:
        while True:
            time.sleep(args.interval)
            new = False
            for ev in collect(offsets, args.root, since):
                emit(ev, last_model, last_msg, tally, filtered, flt,
                     do_print=True, src_filt=args.filter)
                new = True
            if pin and new:
                pin.draw(pin_lines())
    except KeyboardInterrupt:
        pass
    finally:
        if pin:
            pin.stop()
    print("\n" + (filtered.summary("displayed turns") if active
                  else tally.summary(tally_label)))
    if SKIPPED["n"]:
        print(C.dim(f"  ({SKIPPED['n']} unparseable transcript records skipped)"))
    print(C.dim("stopped."))


if __name__ == "__main__":
    main()
