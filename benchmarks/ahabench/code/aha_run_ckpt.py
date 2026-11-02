"""Resumable version of aha_run.py: same episode loop as benchmark.run_episode (AhaBench
scripts/run_v2.py), plus a checkpoint at every new simulated day, like the paper's own V1
runner (scripts/run_v1.py). Rerun the same command to resume after an interruption.

Usage: python -I aha_run_ckpt.py --arm none --seed N --out DIR [--model M]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pickle
import sys
import time
from pathlib import Path

from open_vending_bench.agents.runtime import AgentRuntimeConfig, build_agent
from open_vending_bench.benchmark import build_environment
from open_vending_bench._no_order_watchdog import NoOrderWatchdog
from open_vending_bench._tool_call_watchdog import ToolCallSuccessRateWatchdog
from open_vending_bench.utils import slugify, write_json

PROVIDER = str(Path(__file__).resolve().parent / "logbook_provider.py") + ":AnthropicLogbookProvider"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["none", "notes", "logbook", "generic", "generic2", "logbook_noout", "auto", "auto2"])
    ap.add_argument("--max-day", type=int, default=None, help="stop after this simulated day")
    ap.add_argument("--label", default=None, help="run name (default: the arm)")
    ap.add_argument("--logbook-file", default=None, help="for --arm auto or auto2: the written Logbook to load")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--scenario", default="v2")
    ap.add_argument("--mode", default="calibrated")
    ap.add_argument("--max-output-tokens", type=int, default=8000)
    args = ap.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    label = args.label or args.arm
    stem = f"{label}_seed{args.seed}"
    if (out / f"{stem}.json").exists():
        print("already finished")
        return 0
    os.environ["AHA_ARM"] = args.arm
    if args.logbook_file:
        os.environ["AHA_LOGBOOK_FILE"] = str(Path(args.logbook_file).resolve())
    os.environ["AHA_USAGE_LOG"] = str(out / f"{stem}.usage.json")
    os.environ["AHA_TRACE_LOG"] = str(out / f"{stem}.calls.jsonl")
    ts_path, ckpt = out / f"{stem}.timeseries.csv", out / f"{stem}.ckpt.pkl"

    runtime = AgentRuntimeConfig(
        backend="live_api", profile_name=label, provider="python", model=args.model,
        provider_class=PROVIDER, max_output_tokens=args.max_output_tokens, timeout_seconds=180.0)
    agent = build_agent(runtime)
    provider = agent.provider._instance
    rows: list[dict] = []
    t0 = time.time()

    if ckpt.exists():
        with ckpt.open("rb") as fh:
            saved = pickle.load(fh)
        env = saved["env"]
        agent.reset(seed=args.seed, config=env.config)
        agent.step_index = saved["agent"]["step_index"]
        agent.recent_history = list(saved["agent"]["recent_history"])
        provider.totals.update(saved["provider"]["totals"])
        usage_file = Path(os.environ["AHA_USAGE_LOG"])
        if usage_file.exists():  # includes calls made after the checkpoint and lost by the interruption
            u = json.loads(usage_file.read_text())
            provider.totals.update({k: u[k] for k in provider.totals if k in u})
        provider.seed = saved["provider"]["seed"]
        provider.notes = list(saved["provider"]["notes"])
        provider.last_action = saved["provider"]["last_action"]
        if saved["provider"].get("book") is not None:
            provider.book = saved["provider"]["book"]
        rows = saved["rows"]
        t0 -= saved.get("elapsed", 0.0)
        observation = env.observe()
        print(f"resumed at {env.now.isoformat()} step {agent.step_index}", flush=True)
    else:
        env = build_environment(mode=args.mode, scenario=args.scenario)
        agent.reset(seed=args.seed, config=env.config)
        observation = env.reset(args.seed)

    def snap():
        inv = env.inventory_value()
        day = (env.now.date() - env.config.start_datetime.date()).days + 1
        rows.append({"day": day, "date": env.now.date().isoformat(), "bank": round(env.bank_balance, 2),
                     "machine_cash": round(env.machine_cash, 2), "inventory_value": round(inv, 2),
                     "net_worth": round(env.bank_balance + env.machine_cash + inv, 2),
                     "net_profit": round(env.bank_balance + env.machine_cash + inv - env.config.starting_balance, 2),
                     "security_losses": round(getattr(env, "security_losses_total", 0.0), 2),
                     "wall": round(time.time(), 1)})
        with ts_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    def save():
        tmp = ckpt.with_suffix(".tmp")
        try:
            _save(tmp)
        except Exception as exc:  # noqa: BLE001 - e.g. a written Logbook that cannot be pickled
            tmp.unlink(missing_ok=True)
            print(f"checkpoint skipped: {type(exc).__name__}: {exc}"[:200], flush=True)

    def _save(tmp):
        with tmp.open("wb") as fh:
            pickle.dump({"env": env, "rows": rows, "elapsed": time.time() - t0,
                         "agent": {"step_index": agent.step_index, "recent_history": list(agent.recent_history)},
                         "provider": {"totals": dict(provider.totals), "seed": provider.seed,
                                      "notes": list(provider.notes), "last_action": provider.last_action,
                                      "book": provider.book}}, fh)
        os.replace(tmp, ckpt)

    if not rows:
        snap()
        save()
    # the episode loop of benchmark.run_episode; watchdogs start fresh on resume, as in run_v1.py
    watchdog = NoOrderWatchdog(env, threshold_days=20)
    tool_call_watchdog = ToolCallSuccessRateWatchdog(agent, min_success_rate=0.80, window=50, warmup_calls=50)
    current = env.now.date()
    while not observation.done:
        previous_observation = observation
        action = agent.choose_action(observation)
        observation = env.execute(action)
        agent.observe_transition(previous_observation, action, observation)
        agent.last_action = action
        if env.now.date() != current:
            current = env.now.date()
            snap()
            save()
            if args.max_day and rows[-1]["day"] > args.max_day:
                break
        if watchdog.should_abort(env, observation):
            if getattr(env, "termination_reason", None) is None:
                env.termination_reason = watchdog.termination_reason
            observation = env.observe()
            break
        if tool_call_watchdog.should_abort():
            rate = tool_call_watchdog.current_success_rate()
            reason = (f"{tool_call_watchdog.termination_reason} (rate={rate:.2f} calls={tool_call_watchdog.total_calls()}"
                      f" fallbacks={tool_call_watchdog.total_fallbacks()})")
            if getattr(env, "termination_reason", None) is None:
                env.termination_reason = reason
            observation = env.observe()
            break
    snap()
    trace_file = out / "traces" / f"{slugify(agent.name)}_seed{args.seed}.trace.jsonl"
    env.dump_trace(trace_file)
    summary = env.summary(profile_name=agent.name, trace_path=str(trace_file))
    payload = summary.to_dict()
    payload["final_net_profit"] = round(payload["final_net_worth"] - env.config.starting_balance, 2)
    payload["arm"] = label
    if args.max_day and not getattr(env, "termination_reason", None) and rows[-1]["day"] > args.max_day:
        payload["termination_reason"] = f"stopped_after_day_{args.max_day}"
    payload["logbook_file"] = args.logbook_file
    payload["model"] = args.model
    payload["wall_seconds"] = round(time.time() - t0, 1)
    payload["usage"] = dict(provider.totals)
    write_json(out / f"{stem}.json", payload)
    print(json.dumps({k: payload.get(k) for k in ("arm", "seed", "termination_reason", "simulated_days",
                                                  "final_net_worth", "final_net_profit", "units_sold")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
