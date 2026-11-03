import re


def _rows(result):
    """Parse a fixed-width result into (header_tokens, row_lines)."""
    lines = [l for l in result.split("\n")]
    for i, l in enumerate(lines):
        s = l.strip()
        if s and set(s) <= set("- ") and "-" in s and i > 0:
            header = lines[i - 1].split()
            rows = [r for r in lines[i + 1:] if r.strip()]
            return header, rows
    return [], []


def _short(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


class Logbook:
    def __init__(self) -> None:
        self.history = []          # finished questions
        self.cur = None            # current question record
        self.tables = []           # table names in discovery order
        self.cols = {}             # table -> list of columns
        self.errors = []           # (sql, error) across run
        self.budget = None

    # ---------- parsing helpers ----------
    def _new_cur(self, seen):
        q = ""
        m = re.search(r"Question \d+/\d+\s*\n\s*\n(.*?)\n\s*\n", seen, re.S)
        if m:
            q = m.group(1).strip()
        m = re.search(r"up to (\d+) exploratory", seen)
        if m:
            self.budget = int(m.group(1))
        self.cur = {"q": q, "queries": [], "used": 0}

    def _add_table(self, t):
        t = t.strip().strip('"`[]')
        if t and re.match(r"^\w+$", t) and t.lower() not in ("name", "sqlite_sequence"):
            if t not in self.tables:
                self.tables.append(t)

    def _add_cols(self, t, cols):
        self._add_table(t)
        t = t.strip().strip('"`[]')
        lst = self.cols.setdefault(t, [])
        for c in cols:
            c = c.strip().strip('"`[]')
            if c and re.match(r"^\w+$", c) and c.upper() not in (
                    "PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK", "KEY") and c not in lst:
                lst.append(c)

    def _learn_schema(self, sql, result):
        low = sql.lower()
        # CREATE statements anywhere in output
        for m in re.finditer(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?[\"`\[]?(\w+)[\"`\]]?\s*\(", result, re.I):
            body = result[m.end(): m.end() + 1500]
            nxt = re.search(r"CREATE TABLE", body, re.I)
            if nxt:
                body = body[: nxt.start()]
            cols = []
            for part in re.split(r",", body):
                tok = part.strip().split()
                if tok:
                    cols.append(tok[0])
            self._add_cols(m.group(1), cols)
        header, rows = _rows(result)
        if ("sqlite_master" in low or low.strip().startswith(".tables")) and "create" not in result.lower()[:0]:
            if header and header[0].lower() in ("name", "tbl_name"):
                for r in rows:
                    tok = r.split()
                    if tok:
                        self._add_table(tok[0])
            elif low.strip().startswith(".tables"):
                for tok in result.split():
                    self._add_table(tok)
        m = re.search(r"pragma\s+table_info\s*\(\s*['\"`]?(\w+)", low)
        if m and header and "name" in [h.lower() for h in header]:
            # find the actual case of table name in sql
            m2 = re.search(r"table_info\s*\(\s*['\"`]?(\w+)", sql, re.I)
            cols = []
            for r in rows:
                tok = r.split()
                if len(tok) > 1:
                    cols.append(tok[1])
            self._add_cols(m2.group(1) if m2 else m.group(1), cols)
        m = re.match(r"\s*select\s+\*\s+from\s+[\"`\[]?(\w+)[\"`\]]?(\s+(limit|where|order)\b.*)?\s*;?\s*$", sql, re.I | re.S)
        if m and header and " join " not in low:
            self._add_cols(m.group(1), header)

    # ---------- interface ----------
    def observe(self, step: dict) -> None:
        try:
            action = step.get("action")
            result = step.get("result") or ""
            seen = step.get("seen") or ""
            if action and isinstance(action, dict):
                args = action.get("arguments") or {}
                kind = str(args.get("action", "")).upper()
                content = str(args.get("content", ""))
                if self.cur is None:
                    self.cur = {"q": "", "queries": [], "used": 0}
                verdict = re.search(r"Question \d+:\s*(CORRECT|INCORRECT)", result)
                if kind == "QUERY" and not verdict:
                    err = None
                    if re.search(r"\berror\b|no such|syntax", result[:300], re.I) and "Query result" not in result[:40]:
                        err = _short(result, 160)
                    elif re.search(r"^\s*(Error|SQL error)", result.split("\n", 1)[-1] if "\n" in result else result, re.I):
                        err = _short(result, 160)
                    m = re.search(r"\((\d+)/(\d+) queries used", result)
                    if m:
                        self.cur["used"] = int(m.group(1))
                        self.budget = int(m.group(2))
                    else:
                        self.cur["used"] += 1
                    if err:
                        self.cur["queries"].append((content, "ERROR: " + err))
                        self.errors.append((content, err))
                    else:
                        header, rows = _rows(result)
                        first = _short(rows[0], 70) if rows else "(no rows)"
                        n = len(rows)
                        out = "%d row%s%s; first: %s" % (n, "" if n == 1 else "s",
                                                         " (capped)" if n >= 50 else "", first)
                        self.cur["queries"].append((content, out))
                        try:
                            self._learn_schema(content, result)
                        except Exception:
                            pass
                if verdict:
                    ca = re.search(r"Correct answer:\s*(.*)", result)
                    ua = re.search(r"Your answer:\s*(.*)", result)
                    qu = re.search(r"Exploratory queries used:\s*(\d+)", result)
                    self.history.append({
                        "q": self.cur.get("q", ""),
                        "ok": verdict.group(1) == "CORRECT",
                        "ans": (ua.group(1).strip() if ua else content),
                        "correct": ca.group(1).strip() if ca else "",
                        "used": int(qu.group(1)) if qu else self.cur.get("used", 0),
                    })
                    self.cur = None
            if self.cur is None and re.search(r"Question \d+/\d+", seen):
                self._new_cur(seen)
            elif self.cur is not None and not self.cur.get("q") and re.search(r"Question \d+/\d+", seen):
                used, qs = self.cur["used"], self.cur["queries"]
                self._new_cur(seen)
                self.cur["used"], self.cur["queries"] = used, qs
        except Exception:
            pass

    def brief(self) -> str:
        try:
            out = []
            b = self.budget or "?"
            # 1. current question
            out.append("== CURRENT QUESTION ==")
            if self.cur:
                out.append("Queries used: %d/%s. Each query costs reward; a wrong answer costs the full budget."
                           % (self.cur["used"], b))
                for i, (sql, res) in enumerate(self.cur["queries"][-8:], 1):
                    out.append(" Q%d: %s -> %s" % (i, _short(sql, 110), res))
            else:
                out.append("(none active)")
            # 2. outcomes
            out.append("== PAST QUESTIONS (this run) ==")
            if self.history:
                ok = [h for h in self.history if h["ok"]]
                bad = [h for h in self.history if not h["ok"]]
                avg = (sum(h["used"] for h in ok) / len(ok)) if ok else 0
                out.append("Correct %d, incorrect %d. Avg queries on correct: %.1f; on incorrect: %s"
                           % (len(ok), len(bad), avg,
                              ("%.1f" % (sum(h["used"] for h in bad) / len(bad))) if bad else "-"))
                for h in self.history[-8:]:
                    if h["ok"]:
                        out.append(" OK  (%dq) %s -> %s" % (h["used"], _short(h["q"], 70), _short(h["ans"], 40)))
                    else:
                        out.append(" BAD (%dq) %s -> gave %s, correct %s" % (
                            h["used"], _short(h["q"], 70), _short(h["ans"], 30), _short(h["correct"], 40)))
            else:
                out.append("(none yet)")
            # 3. schema
            out.append("== SCHEMA SEEN IN THIS RUN ==")
            if self.tables:
                parts = []
                for t in self.tables:
                    c = self.cols.get(t)
                    parts.append("%s(%s)" % (t, _short(", ".join(c), 160)) if c else t + "(cols unseen)")
                out.append(_short("; ".join(parts), 1300))
            else:
                out.append("(nothing yet)")
            # 4. errors
            out.append("== FAILED QUERIES (recent) ==")
            if self.errors:
                for sql, err in self.errors[-4:]:
                    out.append(" %s -> %s" % (_short(sql, 90), _short(err, 110)))
            else:
                out.append("(none)")
            text = "\n".join(out)
            return text[:3990]
        except Exception:
            return "Logbook unavailable."
