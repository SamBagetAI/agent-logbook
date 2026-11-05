import re

_PATH_RE = re.compile(r"(?<![\w$])((?:[\w.-]+/)*[\w.-]+\.(?:py|pyi|cfg|toml|txt|rst|md|ini|json|ya?ml|js|ts|c|h|cpp|go|rs|java|rb|sh))\b")
_RC_RE = re.compile(r"<returncode>\s*(-?\d+)\s*</returncode>")
_OUT_RE = re.compile(r"<output>(.*?)</output>", re.S)
_SUMMARY_RE = re.compile(r"(\d+ (?:passed|failed|error|errors)[^\n]*?) in [\d.]+s")
_TEST_RE = re.compile(r"\b(pytest|py\.test|tox|unittest|nosetests|runtests|manage\.py test)\b")
_ERR_RE = re.compile(r"(Error|error:|No such file|not found|Traceback|Exception|FAILED|cannot|invalid)")
_EDIT_RE = re.compile(r"(sed\s+-i|\bcat\s*>|\bcat\s+<<[^\n]*>|\btee\b|>\s*[\w./-]+\.\w+|\bpatch\b|git apply|\.write\(|perl\s+-[a-z]*i)")


def _short(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _is_script(p):
    b = p.split("/")[-1].lower()
    return p.startswith("/tmp") or b.startswith(("repro", "test_repro", "debug", "check", "verify", "tmp")) or "reproduce" in b


class Register:
    def __init__(self) -> None:
        self.issue_no = 0
        self.repo = "?"
        self.title = ""
        self.steps = 0
        self.edited = []          # files edited this issue (source)
        self.scripts = []         # helper scripts this issue
        self.edit_pending = False  # source edited, no successful run since
        self.ran_after_edit = False
        self.last_run = ""
        self.fails = []           # (cmd, reason) this issue
        self.history = []         # finished issues
        self.repos = {}           # repo -> knowledge

    # ---------- helpers ----------
    def _rk(self):
        r = self.repos.get(self.repo)
        if r is None:
            r = {"layout": "", "tests": {}, "paths": {}, "edited": {}, "fails": {}}
            self.repos[self.repo] = r
        return r

    def _start_issue(self, seen):
        m = re.search(r"Please solve this issue:\s*(.*)", seen)
        self.title = _short(m.group(1), 90) if m else ""
        rm = re.findall(r"Repository:\s*(\S+)", seen)
        self.repo = rm[-1] if rm else "?"
        im = re.search(r"--- Issue (\d+)/(\d+) ---", seen)
        self.issue_no = int(im.group(1)) if im else self.issue_no + 1
        self.issue_total = im.group(2) if im else "?"
        self.steps = 0
        self.edited, self.scripts, self.fails = [], [], []
        self.edit_pending = False
        self.ran_after_edit = False
        self.last_run = ""

    def _finish(self, outcome):
        self.history.append({
            "n": self.issue_no, "repo": self.repo, "steps": self.steps,
            "edits": len(self.edited), "tested": self.ran_after_edit,
            "outcome": _short(outcome, 70),
        })
        self.edited, self.scripts = [], []

    # ---------- interface ----------
    def observe(self, step: dict) -> None:
        try:
            self._observe(step)
        except Exception:
            pass

    def _observe(self, step):
        action = step.get("action")
        result = str(step.get("result") or "")
        seen = str(step.get("seen") or "")
        if action:
            args = action.get("arguments") or {}
            cmd = args.get("command", "") if isinstance(args, dict) else str(args)
            cmd = str(cmd or "")
            self.steps += 1
            self._process(cmd, result)
        if "Please solve this issue:" in seen:
            self._start_issue(seen)
        else:
            rm = re.findall(r"Repository:\s*(\S+)", seen)
            if rm and self.repo == "?":
                self.repo = rm[-1]

    def _process(self, cmd, result):
        rk = self._rk()
        if "Submission" in result or "COMPLETE_TASK_AND_SUBMIT" in cmd:
            m = re.search(r"Submission[^\n]*", result)
            self._finish(m.group(0) if m else _short(result, 70) or "submitted")
            return
        rcm = _RC_RE.search(result)
        rc = int(rcm.group(1)) if rcm else None
        om = _OUT_RE.search(result)
        out = om.group(1) if om else result
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        c = cmd.strip()
        # layout
        if re.fullmatch(r"(cd \S+ && )?ls( -\w+)?", c) and rc == 0 and not rk["layout"]:
            names = [l.split()[-1] for l in lines if l.split()]
            rk["layout"] = _short(" ".join(names), 220)
        # paths seen
        for p in set(_PATH_RE.findall(out + " " + cmd)):
            if "/" in p and not _is_script(p):
                rk["paths"][p] = rk["paths"].get(p, 0) + 1
        is_edit = bool(_EDIT_RE.search(cmd)) and not re.match(r"\s*(grep|nl|sed -n|ls|find)\b", c)
        is_test = bool(_TEST_RE.search(cmd))
        is_run = is_test or bool(re.search(r"\bpython3?\b", cmd)) and not is_edit
        if is_edit:
            files = _PATH_RE.findall(cmd)
            for f in files:
                tgt = self.scripts if _is_script(f) else self.edited
                if f not in tgt:
                    tgt.append(f)
                if not _is_script(f):
                    self.edit_pending = True
                    rk["edited"].setdefault(f, [])
                    if self.issue_no not in rk["edited"][f]:
                        rk["edited"][f].append(self.issue_no)
        reason = ""
        if rc not in (None, 0) or (lines and _ERR_RE.search(lines[-1]) and is_run):
            errs = [l for l in lines if _ERR_RE.search(l)]
            reason = _short((errs[-1] if errs else (lines[-1] if lines else "rc=%s" % rc)), 110)
        if is_run:
            sm = _SUMMARY_RE.findall(out)
            summ = sm[-1] if sm else ("ok" if rc == 0 and not reason else (reason or "rc=%s" % rc))
            self.last_run = "%s -> rc=%s %s" % (_short(cmd, 60), rc, _short(summ, 70))
            if rc == 0 and self.edit_pending:
                self.ran_after_edit = True
                self.edit_pending = False
            if is_test:
                key = _short(cmd, 90)
                rk["tests"][key] = "rc=%s %s" % (rc, _short(summ, 60))
        if reason and rc not in (None, 0):
            self.fails.append((_short(cmd, 60), reason))
            k = reason[:60]
            rk["fails"][k] = rk["fails"].get(k, 0) + 1

    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "Register unavailable."

    def _brief(self):
        o = []
        o.append("## CURRENT ISSUE %s/%s | repo %s | steps used: %d" % (
            self.issue_no, getattr(self, "issue_total", "?"), self.repo, self.steps))
        if self.title:
            o.append("Issue: " + self.title)
        o.append("Source files edited: " + (", ".join(self.edited[-6:]) if self.edited else "none yet"))
        if self.edited:
            o.append("Successful run since last source edit: " + ("yes" if not self.edit_pending else "NO"))
        if self.last_run:
            o.append("Last run: " + self.last_run)
        if self.fails:
            o.append("Failed commands this issue (last 3):")
            for c, r in self.fails[-3:]:
                o.append("  - `%s` -> %s" % (c, r))

        o.append("\n## SUBMISSION OUTCOMES (this run)")
        if not self.history:
            o.append("none yet")
        else:
            for h in self.history[-8:]:
                o.append("  #%s %s: %d steps, %d src edits, ran-after-edit=%s -> %s" % (
                    h["n"], h["repo"], h["steps"], h["edits"],
                    "y" if h["tested"] else "n", h["outcome"]))
            ok = [h for h in self.history if "FAIL" not in h["outcome"].upper()]
            noedit = [h for h in self.history if h["edits"] == 0]
            o.append("  passed-ish: %d/%d; submissions with 0 src edits: %d (%d failed)" % (
                len(ok), len(self.history), len(noedit),
                sum(1 for h in noedit if "FAIL" in h["outcome"].upper())))

        rk = self.repos.get(self.repo)
        o.append("\n## REPO KNOWLEDGE: %s" % self.repo)
        if not rk:
            o.append("nothing recorded yet")
        else:
            if rk["layout"]:
                o.append("Root: " + rk["layout"])
            if rk["tests"]:
                o.append("Test commands tried:")
                for k, v in list(rk["tests"].items())[-5:]:
                    o.append("  `%s` -> %s" % (k, v))
            if rk["edited"]:
                o.append("Files edited (issue #s): " + _short(", ".join(
                    "%s%s" % (f, v) for f, v in list(rk["edited"].items())[-8:]), 400))
            if rk["paths"]:
                top = sorted(rk["paths"].items(), key=lambda x: -x[1])[:10]
                o.append("Most-seen paths: " + _short(", ".join(p for p, _ in top), 500))
            if rk["fails"]:
                top = sorted(rk["fails"].items(), key=lambda x: -x[1])[:4]
                o.append("Recurring errors: " + "; ".join("%s (x%d)" % (k, v) for k, v in top))
        others = [r for r in self.repos if r != self.repo and r != "?"]
        if others:
            o.append("Other repos seen: " + ", ".join(others[-5:]))
        return "\n".join(o)
