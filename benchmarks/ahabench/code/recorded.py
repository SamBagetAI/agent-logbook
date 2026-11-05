"""Names in the published AhaBench runs, which were recorded before the Register got its name.

Until 4 November 2026 the Register was called the Logbook. The runs in ../data were recorded under
that name and keep it: in their file names (logbook_seed3.json), in the keys inside them
(logbook_errors) and in the text the agent was shown. The analysis scripts read them through
these three functions.
"""
from pathlib import Path

FORMER = "logbook"


def recorded(name: str) -> str:
    """A current name as the published runs spell it: register_noout_seed3 -> logbook_noout_seed3."""
    return (name.replace("register", FORMER).replace("Register", FORMER.capitalize())
            .replace("REGISTER", FORMER.upper()))


def current(name: str) -> str:
    """A name or a text from the published runs as it is spelled now: logbook_errors -> register_errors."""
    return (name.replace(FORMER, "register").replace(FORMER.capitalize(), "Register")
            .replace(FORMER.upper(), "REGISTER"))


def run_stem(root, arm: str, seed: int) -> str:
    """The file stem of a run in root: <arm>_seed<seed> for a run made now, else the published runs' name."""
    stem = f"{arm}_seed{seed}"
    return stem if any(Path(root).glob(stem + ".*")) else recorded(stem)
