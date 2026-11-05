"""Run one Aha-Vending episode exactly like the paper's V2 runner (scripts/run_v2.py ->
benchmark.run_episode), with one addition: a daily snapshot of net worth, the same row the
paper's V1 runner writes (bank + machine cash + inventory value), so the early vs late
earning rate can be computed for V2 too. The snapshot only reads the environment.

Usage: python -I aha_run.py --arm none|notes|register --seed N --out DIR [--model M]
The arm is passed to the provider through AHA_ARM.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import open_vending_bench.benchmark as B
from open_vending_bench.agents.runtime import AgentRuntimeConfig
from open_vending_bench.utils import write_json

PROVIDER = str(Path(__file__).resolve().parent / "register_provider.py") + ":AnthropicRegisterProvider"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["none", "notes", "register", "register_checks", "generic", "generic2", "register_noout"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--scenario", default="v2")
    ap.add_argument("--mode", default="calibrated")
    ap.add_argument("--max-output-tokens", type=int, default=8000)
    args = ap.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{args.arm}_seed{args.seed}"
    os.environ["AHA_ARM"] = args.arm
    os.environ["AHA_USAGE_LOG"] = str(out / f"{stem}.usage.json")
    os.environ["AHA_TRACE_LOG"] = str(out / f"{stem}.calls.jsonl")
    ts_path = out / f"{stem}.timeseries.csv"
    rows: list[dict] = []

    def snap(env):
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

    original_build = B.build_environment

    def build_environment(*a, **k):
        env = original_build(*a, **k)
        original_reset, original_execute = env.reset, env.execute
        state = {"date": None}

        def reset(seed):
            obs = original_reset(seed)
            state["date"] = env.now.date()
            snap(env)
            return obs

        def execute(action):
            obs = original_execute(action)
            if env.now.date() != state["date"]:
                state["date"] = env.now.date()
                snap(env)
            return obs

        env.reset, env.execute = reset, execute
        return env

    B.build_environment = build_environment
    runtime = AgentRuntimeConfig(
        backend="live_api", profile_name=f"{args.arm}", provider="python", model=args.model,
        provider_class=PROVIDER, max_output_tokens=args.max_output_tokens, timeout_seconds=180.0)
    t0 = time.time()
    summary, env = B.run_episode(profile_name=runtime.profile_name, seed=args.seed, mode=args.mode,
                                 scenario=args.scenario, trace_dir=out / "traces", agent_runtime=runtime)
    snap(env)
    payload = summary.to_dict()
    payload["final_net_profit"] = round(payload["final_net_worth"] - env.config.starting_balance, 2)
    payload["arm"] = args.arm
    payload["wall_seconds"] = round(time.time() - t0, 1)
    try:
        payload["usage"] = json.loads(Path(os.environ["AHA_USAGE_LOG"]).read_text())
    except Exception:  # noqa: BLE001
        pass
    write_json(out / f"{stem}.json", payload)
    print(json.dumps({k: payload.get(k) for k in ("arm", "seed", "termination_reason", "simulated_days",
                                                  "final_net_worth", "final_net_profit", "units_sold")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
