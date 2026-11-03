"""Rewrite a Logbook in the middle of a run, from what the run has shown so far.

The author wrote the Logbook from a spec, before the run. After some decisions, the run shows what
the spec could not: a counterpart that changes, a field that matters, a text the agent ignores. Here
the author reads its own Logbook and the run so far, as the agent saw it, and rewrites it. The new
Logbook replays every decision so far, so its record is complete, and takes over.

In a loop, with a LogbookAdapter (adapter.py), after some decisions:

    from rewrite import rewrite_mid_run
    new = rewrite_mid_run(book, "my_agent/spec.json", name="my_logbook_2")   # None if the author failed
    ...the loop goes on unchanged; book now runs the new Logbook

Offline, on a trace written with trace=..., trace_seen=True:

    python rewrite.py spec.json --logbook written/my_logbook.py --trace trial/trace.jsonl --at 80 --name my_logbook_2

This cuts the trace after 80 decisions, has the author rewrite the Logbook from them, then replays
the whole trace through the new Logbook, the decisions after the cut included, and prints what it
found: errors, text lengths, the new text at the cut and at the end. Both ways write
<out>/<name>_input/ (the report and the steps the author got) and <out>/<name>.py and .json.
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
import time
from pathlib import Path

from adapter import LogbookAdapter, load_logbook, steps_from_rows
from trial_report import report

HERE = Path(__file__).resolve().parent
GOES_ON = "(the run goes on from here)"


def write_inputs(rows: list[dict], folder: Path) -> tuple[Path, Path]:
    """What the author gets: the report of the run so far, and the steps its drafts are validated on."""
    folder.mkdir(parents=True, exist_ok=True)
    text, _ = report(rows, seen=True, end=GOES_ON)
    (folder / "report.txt").write_text(text, encoding="utf-8")
    steps = folder / "steps.json.gz"
    steps.write_bytes(gzip.compress(json.dumps(steps_from_rows(rows), ensure_ascii=False).encode(), mtime=0))
    return folder / "report.txt", steps


def run_author(spec: Path, logbook: Path, report_path: Path, steps: Path, name: str, out: Path,
               author: str = "claude-opus-5-5", outcome: str = "", past_runs: bool = False,
               key_file: Path | None = None, max_tokens: int = 16000) -> Path | None:
    """author.py in same-run mode. The new Logbook's path, or None when the author failed. A Logbook
    already written under this name is reused."""
    log = out / f"{name}.json"
    if not log.exists():
        cmd = [sys.executable, str(HERE / "author.py"), str(spec), "--name", name, "--out", str(out), "--author", author,
               "--revise-from", str(logbook), "--trial-report", str(report_path), "--same-run",
               "--steps-file", str(steps), "--trial-outcome", outcome, "--max-tokens", str(max_tokens)]
        cmd += ["--past-runs"] if past_runs else []
        cmd += ["--key-file", str(key_file)] if key_file else []
        r = subprocess.run(cmd, capture_output=True, text=True)
        if not log.exists():
            print((r.stdout + r.stderr)[-3000:], file=sys.stderr)
            return None
    return out / f"{name}.py" if json.loads(log.read_text()).get("final_ok") else None


def rewrite_mid_run(book: LogbookAdapter, spec: str | Path, name: str, out: str | Path = "written",
                    author: str = "claude-opus-5-5", outcome: str = "", past_runs: bool = False,
                    key_file: str | Path | None = None) -> Path | None:
    """The author rewrites the Logbook that `book` runs, from the run so far; the new one replays every
    decision and takes over. None, and the current Logbook stays, when the author failed."""
    if book.path is None:
        raise ValueError("no Logbook to rewrite: the adapter runs without one")
    out = Path(out)
    report_path, steps = write_inputs(book.rows, out / f"{name}_input")
    path = run_author(Path(spec), Path(book.path), report_path, steps, name, out, author, outcome, past_runs,
                      Path(key_file) if key_file else None)
    if path is None:
        return None
    return path if book.rewrite(path)["ok"] else None


def replay(path: Path, rows: list[dict], show_at: tuple[int, ...]) -> tuple[dict, dict]:
    """The whole trace through the Logbook at path: errors, lengths, speed, and its text at the given decisions."""
    book = load_logbook(path)
    errors, longest, slowest, texts = 0, 0, 0.0, {}
    for i, step in enumerate(steps_from_rows(rows), 1):
        t = time.perf_counter()
        try:
            book.observe(step)
            text = book.brief()
            longest = max(longest, len(text))
        except Exception as exc:  # noqa: BLE001
            errors, text = errors + 1, f"(error: {exc!r})"
        slowest = max(slowest, time.perf_counter() - t)
        if i in show_at:
            texts[i] = text
    return {"decisions": len(rows), "errors": errors, "longest_brief": longest, "slowest_call_s": round(slowest, 3)}, texts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path, help="the agent spec (JSON)")
    ap.add_argument("--logbook", type=Path, required=True, help="the Logbook the run used")
    ap.add_argument("--trace", type=Path, required=True, help="the run's trace, with 'seen' (adapter.py, trace_seen=True)")
    ap.add_argument("--at", type=int, required=True, help="rewrite after this many decisions")
    ap.add_argument("--name", required=True, help="name of the Logbook to write")
    ap.add_argument("--out", type=Path, default=Path("written"))
    ap.add_argument("--author", default="claude-opus-5-5")
    ap.add_argument("--outcome", default="", help="one sentence on the run so far, e.g. 'After 30 hands: -120 chips.'")
    ap.add_argument("--past-runs", action="store_true", help="the spec's past run was shown when the Logbook was written")
    ap.add_argument("--key-file", type=Path, default=None)
    a = ap.parse_args()
    rows = [json.loads(line) for line in a.trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any("seen" not in r for r in rows):
        raise SystemExit("the trace has no 'seen': record the run with trace_seen=True")
    so_far = [dict(r) for r in rows[:a.at]]
    so_far[-1]["action"] = so_far[-1]["result"] = None  # the decision the agent is about to make
    report_path, steps = write_inputs(so_far, a.out / f"{a.name}_input")
    print(json.dumps({"decisions_shown": len(so_far), "report_chars": len(report_path.read_text())}))
    path = run_author(a.spec, a.logbook, report_path, steps, a.name, a.out, a.author, a.outcome, a.past_runs, a.key_file)
    if path is None:
        print("the author failed; see", a.out / f"{a.name}.json")
        return 1
    summary, texts = replay(path, rows, (a.at, len(rows)))
    (a.out / f"{a.name}_input" / "replay.json").write_text(json.dumps({**summary, "logbook": str(path)}, indent=1))
    print(json.dumps({"logbook": str(path), **summary}))
    for i, text in texts.items():
        print(f"\n--- the new Logbook's text at decision {i} ---\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
