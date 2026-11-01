"""Prosus Vending Bench with Claude through the Anthropic API, notes vs Logbook.

This is the benchmark's own MCP-only runner (scripts/run_openrouter.py) with the
transport swapped from OpenRouter to the Anthropic API through LiteLLM. Same
briefing, same 22 business tools, same rolling window of complete exchanges,
same memory policy, same scoring. Two arms:

  notes    the benchmark as published: the agent keeps memory with write_note,
           read_notes and reminders
  logbook  the same, plus a Logbook layer between the agent and the tools: it
           records every action and fact automatically, appends a short brief
           to get_status and wait_for_next_day, and refuses a few actions the
           briefing says are irreversible mistakes

  python run_vb.py --arm notes --model claude-haiku-5-5 \
      --seeds 101 102 --output runs/dev-notes
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import count
from pathlib import Path
import multiprocessing

ROOT = Path(os.environ.get("VENDING_BENCH_ROOT", "vending-bench"))  # clone of ProsusAI/vending-bench
SIM = ROOT / "tasks/vending-bench/environment/sim-server"
HERE = Path(__file__).resolve().parent

EUR_PER_USD = 0.92
# Verbatim from scripts/run_openrouter.py
MEMORY_POLICY = ('Conversation history uses a rolling window of complete tool exchanges. '
                 'Older exchanges may disappear. Use write_note and read_notes to retain '
                 'your own business plans and discoveries. Continue using the business tools '
                 'until the simulation ends. You may request multiple tools per response; '
                 'they execute sequentially in the order you give them.')
CACHE = [{"location": "message", "role": "system"}]


def _api_key() -> str:
    """The Anthropic key: ANTHROPIC_API_KEY, or a file named by ANTHROPIC_KEY_FILE."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"].strip()
    path = os.environ.get("ANTHROPIC_KEY_FILE")
    if path and Path(path).exists():
        return Path(path).read_text().strip()
    raise RuntimeError("Set ANTHROPIC_API_KEY (or ANTHROPIC_KEY_FILE) before running.")


def write_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    tmp.replace(path)


def window(initial, exchanges, max_chars):
    """Verbatim from run_openrouter.py: keep whole assistant/tool groups."""
    selected = []
    size = len(json.dumps(initial, ensure_ascii=False))
    for exchange in reversed(exchanges):
        length = len(json.dumps(exchange, ensure_ascii=False))
        if selected and size + length > max_chars:
            break
        selected.append(exchange)
        size += length
    return initial + [m for group in reversed(selected) for m in group]


def assistant_dict(message) -> dict:
    out = {"role": "assistant", "content": message.content or ""}
    calls = []
    for c in message.tool_calls or []:
        calls.append({"id": c.id, "type": "function",
                      "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}})
    if calls:
        out["tool_calls"] = calls
    return out


async def execute_tools(client, calls, allowed, layer):
    """run_openrouter.execute_tools, with an optional Logbook layer around each call."""
    results = []
    for call in calls:
        try:
            name = call["function"]["name"]
            if name not in allowed:
                raise ValueError("Unknown business tool")
            arguments = json.loads(call["function"]["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object")
            refusal = layer.check(name, arguments) if layer else None
            if refusal:
                content = refusal
            else:
                result = await client.call_tool(name, arguments, raise_on_error=False)
                content = "\n".join(c.text for c in result.content if getattr(c, "text", None))
                if layer:
                    content = layer.after(name, arguments, content)
        except Exception as exc:  # noqa: BLE001
            content = f"Tool error: {type(exc).__name__}: {exc}"
        results.append({"role": "tool", "tool_call_id": call["id"], "content": content})
    return results


async def play(arm, model, seed, args):
    out = args.output / f"{model}-{arm}-seed{seed}-{args.days}d"
    out.mkdir(parents=True, exist_ok=False)
    for key in list(os.environ):
        if key.startswith("VENDING_"):
            del os.environ[key]
    os.environ.update(VENDING_SEED=str(seed), VENDING_SIM_DAYS=str(args.days),
                      VENDING_STATE_PATH=str(out / "live-state.json"), VENDING_RESUME="0")
    os.environ["ANTHROPIC_API_KEY"] = _api_key()
    sys.path[:0] = [str(SIM), str(ROOT / "scripts"), str(HERE)]
    import litellm
    import server
    import sync_config
    from fastmcp import Client
    from vending import config

    litellm.suppress_debug_info = True
    layer = None
    if arm == "logbook":
        from vb_logbook import Logbook
        layer = Logbook()  # sees tool results only, never the simulator state

    prompt = sync_config.render()
    (out / "instruction.md").write_text(prompt + "\n\n" + MEMORY_POLICY)
    write_json(out / "harness.json", {
        "harness": "run_openrouter.py transport swapped for the Anthropic API via LiteLLM",
        "arm": arm, "model": model, "tools": "Only the 22 MCP business tools",
        "max_output_tokens": args.max_tokens, "context_chars": args.context_chars,
        "memory_policy": MEMORY_POLICY, "reasoning": "provider default (no extended thinking)",
        "temperature": "provider default", "prompt_caching": "system prompt only",
        "max_turns": args.max_turns or None, "timeout_seconds": args.timeout or None,
    })
    initial = [{"role": "system", "content": prompt + "\n\n" + MEMORY_POLICY},
               {"role": "user", "content": "Operate the business until the simulation ends."}]
    exchanges, spent, turns, empty_turns = [], 0.0, 0, 0
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    started = time.monotonic()
    outcome, error = "turn_limit", None
    try:
        async with Client(server.mcp) as client:
            tool_list = await client.list_tools()
            allowed = {t.name for t in tool_list}
            assert len(allowed) == 22
            tools = [{"type": "function", "function": {
                "name": t.name, "description": t.description or "", "parameters": t.inputSchema}}
                for t in tool_list]
            write_json(out / "tools.json", tools)
            for turns in (range(1, args.max_turns + 1) if args.max_turns else count(1)):
                if server.ENGINE.over:
                    outcome = "simulation_ended"
                    break
                if args.timeout and time.monotonic() - started > args.timeout:
                    outcome = "wall_clock_limit"
                    break
                if args.budget and spent > args.budget:
                    outcome = "budget_limit"
                    break
                messages = window(initial, exchanges, args.context_chars)
                resp = None
                for attempt in range(6):
                    try:
                        resp = await litellm.acompletion(
                            model=f"anthropic/{model}", messages=messages, tools=tools,
                            max_tokens=args.max_tokens, cache_control_injection_points=CACHE,
                            num_retries=2, timeout=180)
                        break
                    except Exception as exc:  # noqa: BLE001
                        if attempt == 5:
                            raise
                        await asyncio.sleep(min(60, 4 * 2 ** attempt))
                try:
                    cost = litellm.completion_cost(completion_response=resp)
                except Exception:  # noqa: BLE001
                    cost = 0.0
                spent += cost
                u = resp.usage
                tokens["input"] += u.prompt_tokens or 0
                tokens["output"] += u.completion_tokens or 0
                tokens["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
                tokens["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
                message = assistant_dict(resp.choices[0].message)
                calls = message.get("tool_calls") or []
                exchange = [message]
                if calls:
                    empty_turns = 0
                    exchange += await execute_tools(client, calls, allowed, layer)
                else:
                    empty_turns += 1
                    exchange.append({"role": "user", "content":
                                     "Continue operating with the business tools until the simulation ends."})
                exchanges.append(exchange)
                with (out / "conversation.jsonl").open("a") as log:
                    log.write(json.dumps(exchange, ensure_ascii=False) + "\n")
                write_json(out / "progress.json", {
                    "arm": arm, "model": model, "seed": seed, "turns": turns,
                    "day": server.ENGINE.s["day"], "tool_calls": server.ENGINE.s["tool_calls"],
                    "balance": server.ENGINE.s["balance"], "api_cost_usd": round(spent, 4),
                    "runtime_seconds": round(time.monotonic() - started),
                })
                if server.ENGINE.over:
                    outcome = "simulation_ended"
                    break
                if empty_turns >= 3:
                    outcome = "agent_stopped_calling_tools"
                    break
    except Exception as exc:  # noqa: BLE001
        outcome = "harness_or_api_error"
        error = f"{type(exc).__name__}: {exc}"[:2000]
    score = server.ENGINE.score(finalize=True)
    write_json(out / "trajectory.json", server.ENGINE.s)
    if layer:
        write_json(out / "logbook.json", layer.dump())
    record = {**score, "arm": arm, "model": model, "seed": seed, "horizon_days": args.days,
              "api_cost_usd": round(spent, 4), "tokens": tokens,
              "llm_cost_eur": round(spent * EUR_PER_USD, 2),
              "profit_after_llm_cost": round(score["profit"] - spent * EUR_PER_USD, 2),
              "runtime_seconds": round(time.monotonic() - started), "api_turns": turns,
              "model_result_subtype": outcome, "error": error,
              "refusals": layer.refusals if layer else 0,
              "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_json(out / "result.json", record)
    return record


def worker(arm, model, seed, args):
    return asyncio.run(play(arm, model, seed, args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["notes", "logbook"], required=True)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--seeds", nargs="+", type=int, default=[1])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=0)
    ap.add_argument("--max-turns", type=int, default=0)
    ap.add_argument("--budget", type=float, default=6.0, help="USD per run; 0 = none")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--context-chars", type=int, default=60000)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1,
                             mp_context=multiprocessing.get_context("spawn")) as pool:
        futs = [pool.submit(worker, args.arm, args.model, s, args) for s in args.seeds]
        for f in as_completed(futs):
            r = f.result()
            print(json.dumps({k: r.get(k) for k in ("arm", "model", "seed", "completed", "reward",
                                                     "profit", "api_cost_usd", "runtime_seconds",
                                                     "api_turns", "model_result_subtype", "error",
                                                     "refusals")}), flush=True)


if __name__ == "__main__":
    main()
