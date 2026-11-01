#!/usr/bin/env python3
"""Resolve the exact model that is generating THIS podcast run, from OpenClaw's own records.

A model cannot reliably name itself: asked "which model are you", it answers with a
family name ("GPT-6") or with whatever the default config says. So the `model_used`
metadata must be read mechanically from the run record instead of being authored.

Where the truth lives (OpenClaw >= 2026.9.x):
  ~/.openclaw/agents/<agent>/agent/openclaw-agent.sqlite
    session_nodes              session_key -> current_session_id, entry_json{modelProvider, model}
    trajectory_runtime_events  every LLM step of the run, each tagged {provider, modelId}
The trajectory is preferred: it reflects what actually answered, including a
mid-run failover. The session entry is the fallback (model selected at run start).
If neither is available, `openclaw sessions --json` is tried. Read-only throughout.

Locating the run:
  --cron-job <id>    Scheduled runs. The cron prompt's first line is
                     "[cron:<id> <job name>] ..." — copy <id> from there.
  --session-key <k>  Explicit session key, if known.
  (neither)          Most recently active session of --agent; reported as inferred.

Output (sh, default):
  export MODEL_USED='openai/gpt-6.1-sol'
  export MODEL_SOURCE='trajectory'          # trajectory | session-entry | sessions-cli | unresolved
  export MODEL_FALLBACK=''                   # e.g. 'openai/gpt-6.1-sol -> deepseek/deepseek-v4-flash'
  export MODEL_SESSION_KEY='agent:info-digest-agent:cron:...'
Exit 0 = resolved. Exit 2 = unresolved (MODEL_USED is then the literal 未核实 marker).

Stdlib only. OpenClaw-specific by nature; on other hosts it exits 2 cleanly.
"""

import argparse
import glob
import json
import os
import shlex
import sqlite3
import subprocess
import sys

UNRESOLVED = "未核实（无法取得当次实际模型 ID）"


def state_dir():
    return os.environ.get("OPENCLAW_STATE_DIR") or os.path.expanduser("~/.openclaw")


def agent_dbs(agent):
    root = os.path.join(state_dir(), "agents")
    if agent:
        return [os.path.join(root, agent, "agent", "openclaw-agent.sqlite")]
    return sorted(glob.glob(os.path.join(root, "*", "agent", "openclaw-agent.sqlite")))


def connect_ro(path):
    if not os.path.isfile(path):
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return None


def find_node(conn, cron_job, session_key):
    """Return (session_key, session_id, entry_dict, updated_at) or None."""
    q = "SELECT session_key, current_session_id, entry_json, updated_at FROM session_nodes"
    if session_key:
        rows = conn.execute(q + " WHERE session_key = ?", (session_key,)).fetchall()
    elif cron_job:
        rows = conn.execute(q + " WHERE session_key LIKE ? ORDER BY updated_at DESC",
                            (f"%:cron:{cron_job}",)).fetchall()
    else:
        rows = conn.execute(q + " ORDER BY updated_at DESC LIMIT 1").fetchall()
    if not rows:
        return None
    key, sid, ej, upd = rows[0]
    try:
        entry = json.loads(ej) if ej else {}
    except ValueError:
        entry = {}
    return key, sid, entry, upd


def trajectory_models(conn, session_id):
    """Distinct provider/modelId pairs in first-seen order."""
    seen = []
    try:
        rows = conn.execute(
            "SELECT event_json FROM trajectory_runtime_events WHERE session_id = ? ORDER BY seq",
            (session_id,)).fetchall()
    except sqlite3.Error:
        return seen
    for (ej,) in rows:
        try:
            e = json.loads(ej)
        except (TypeError, ValueError):
            continue
        prov, mid = e.get("provider"), e.get("modelId")
        if prov and mid:
            ref = f"{prov}/{mid}"
            if ref not in seen:
                seen.append(ref)
    return seen


def from_sqlite(agent, cron_job, session_key):
    best = None
    for db in agent_dbs(agent):
        conn = connect_ro(db)
        if conn is None:
            continue
        try:
            node = find_node(conn, cron_job, session_key)
            if node is None:
                continue
            if best is None or node[3] > best[0][3]:
                best = (node, trajectory_models(conn, node[1]))
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    if best is None:
        return None
    (key, _sid, entry, _upd), models = best
    if models:
        return {"models": models, "source": "trajectory", "key": key}
    prov, mid = entry.get("modelProvider"), entry.get("model")
    if prov and mid:
        return {"models": [f"{prov}/{mid}"], "source": "session-entry", "key": key}
    return None


def from_cli(agent, cron_job, session_key):
    cmd = ["openclaw", "sessions", "--json", "--limit", "all"]
    cmd += ["--agent", agent] if agent else ["--all-agents"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        data = json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    sessions = data.get("sessions", data) if isinstance(data, dict) else data
    if not isinstance(sessions, list):
        return None
    if session_key:
        sessions = [s for s in sessions if s.get("key") == session_key]
    elif cron_job:
        sessions = [s for s in sessions if str(s.get("key", "")).endswith(f":cron:{cron_job}")]
    sessions.sort(key=lambda s: s.get("updatedAt") or 0, reverse=True)
    for s in sessions:
        if s.get("modelProvider") and s.get("model"):
            return {"models": [f"{s['modelProvider']}/{s['model']}"], "source": "sessions-cli",
                    "key": s.get("key", "")}
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Print the exact provider/model of the current run.")
    p.add_argument("--cron-job", help="Cron job id, from the prompt's '[cron:<id> ...]' first line.")
    p.add_argument("--session-key", help="Explicit OpenClaw session key.")
    p.add_argument("--agent", help="Agent id (default: search all agents).")
    p.add_argument("--format", choices=("sh", "env", "json"), default="sh")
    args = p.parse_args(argv)

    if args.cron_job and args.cron_job.startswith("cron:"):
        args.cron_job = args.cron_job[5:]

    res = from_sqlite(args.agent, args.cron_job, args.session_key) \
        or from_cli(args.agent, args.cron_job, args.session_key)

    if res:
        models = res["models"]
        # The final answer comes from the last model that ran; earlier ones failed over.
        out = {
            "MODEL_USED": models[-1],
            "MODEL_ALL": " -> ".join(models),
            "MODEL_FALLBACK": " -> ".join(models) if len(models) > 1 else "",
            "MODEL_SOURCE": res["source"] + ("" if (args.cron_job or args.session_key)
                                             else " (inferred: latest session)"),
            "MODEL_SESSION_KEY": res["key"],
        }
        code = 0
    else:
        out = {"MODEL_USED": UNRESOLVED, "MODEL_ALL": "", "MODEL_FALLBACK": "",
               "MODEL_SOURCE": "unresolved", "MODEL_SESSION_KEY": ""}
        code = 2

    if args.format == "json":
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        prefix = "export " if args.format == "sh" else ""
        for k, v in out.items():
            print(f"{prefix}{k}={shlex.quote(v)}")
    return code


if __name__ == "__main__":
    sys.exit(main())
