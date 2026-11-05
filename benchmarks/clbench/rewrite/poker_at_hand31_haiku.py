import re


ST = {"PREFLOP": "P", "FLOP": "F", "TURN": "T", "RIVER": "R"}
ORDER = ["PREFLOP", "FLOP", "TURN", "RIVER"]


def _cards(s):
    try:
        return "".join(a + b for a, b in re.findall(r"\[\s*(\S+)\s*(\S+)\s*\]", s or ""))
    except Exception:
        return ""


def _bucket(r):
    if r < 0.5:
        return "small(<.5p)"
    if r <= 1.0:
        return "med(.5-1p)"
    return "big(>1p)"


def _parse(seen):
    m = re.search(r"Hand #(\d+)\s*-\s*(\w+)", seen)
    if not m:
        return None
    p = {"hand": int(m.group(1)), "street": m.group(2).upper()}
    g = re.search(r"Your position:\s*([^\n]*)", seen)
    p["pos"] = g.group(1).strip() if g else ""
    pl = p["pos"].lower()
    p["ip"] = ("button" in pl) or ("small" in pl)
    g = re.search(r"Your hand:\s*([^\n]*)", seen)
    p["cards"] = _cards(g.group(1)) if g else ""
    g = re.search(r"Board:\s*([^\n]*)", seen)
    p["board"] = _cards(g.group(1)) if g else ""
    g = re.search(r"Pot:\s*(\d+)", seen)
    p["pot"] = int(g.group(1)) if g else 0
    tc = 0
    g = re.search(r"you need (\d+) to call", seen)
    if g:
        tc = int(g.group(1))
    else:
        g = re.search(r"CALL - call the bet \(costs (\d+) chips\)", seen)
        if g:
            tc = int(g.group(1))
    p["to_call"] = tc
    g = re.search(r"Opponent:\s*(\w+)", seen)
    p["opp"] = g.group(1) if g else None
    g = re.search(r"playing against (\w+)", seen)
    p["notice"] = g.group(1) if g else None
    g = re.search(r"Opponent's actions?:\s*([^\n]*)", seen)
    p["opp_act"] = g.group(1).strip() if g else ""
    return p


def _new_opp():
    return {"resp": {}, "first": {}, "vs_chk": {}, "bet_sz": [],
            "bb_limp": 0, "bb_raise": 0, "btn_raise": 0, "btn_limp": 0,
            "hands": 0, "net": 0, "folds": 0, "fold_lost": 0, "sd": []}


class Register:
    def __init__(self):
        self.hands = {}
        self.order = []
        self.opps = {}
        self.cur_opp = "Opponent"
        self.last = None
        self.total = None

    def _os(self, name):
        if name not in self.opps:
            self.opps[name] = _new_opp()
        return self.opps[name]

    def _h(self, n):
        if n not in self.hands:
            self.hands[n] = {"opp": self.cur_opp, "pos": "", "ip": False, "cards": "",
                             "board": "", "us": [], "oppl": [], "streets": set(),
                             "net": None, "sd": "", "end": "", "pot": 0, "seen_pre": False}
            self.order.append(n)
        return self.hands[n]

    def observe(self, step):
        try:
            self._observe(step)
        except Exception:
            pass

    def _observe(self, step):
        act = step.get("action") or None
        res = step.get("result") or ""
        seen = step.get("seen") or ""
        p = _parse(seen) if "Hand #" in seen else None
        if p:
            if p["notice"]:
                self.cur_opp = p["notice"]
            elif p["opp"]:
                self.cur_opp = p["opp"]
            p["opp"] = self.cur_opp
        prev = self.last
        comp = re.search(r"Hand (\d+) complete", res)
        sd = "SHOWDOWN" in res
        if prev is not None and p is not None and prev.get("opp") and p["hand"] != prev["hand"]:
            pass

        # ---- our action on previous decision ----
        if act and prev and isinstance(act, dict):
            args = act.get("arguments") or {}
            a = str(args.get("action", "")).upper().strip()
            try:
                amt = int(args.get("amount") or 0)
            except Exception:
                amt = 0
            n = prev["hand"]
            h = self._h(n)
            opp = self._os(prev.get("opp") or self.cur_opp)
            st = prev["street"]
            s = ST.get(st, "?")
            same = p is not None and p["hand"] == n
            moved = same and p["street"] != st
            if a == "RAISE":
                ratio = amt / float(prev["pot"]) if prev["pot"] else 0
                h["us"].append("%s:R%d(%.1fp)" % (s, amt, ratio))
                out = None
                if comp:
                    out = 1 if sd else 0
                elif same and p["street"] == st and p["to_call"] > 0:
                    out = 2
                elif moved:
                    out = 1
                if out is not None:
                    key = st + "|" + _bucket(ratio)
                    r = opp["resp"].setdefault(st, [0, 0, 0])
                    r[out] += 1
                    rb = opp["resp"].setdefault(key, [0, 0, 0])
                    rb[out] += 1
                    h["oppl"].append(s + ":" + ["FOLD", "call", "RERAISE"][out])
            elif a == "CHECK":
                h["us"].append(s + ":X")
                if st != "PREFLOP" and not prev["ip"]:
                    o = None
                    if same and p["street"] == st and p["to_call"] > 0:
                        o = 1
                        r = p["to_call"] / float(max(1, p["pot"] - p["to_call"]))
                        opp["bet_sz"].append(r)
                        h["oppl"].append("%s:bet%.1fp" % (s, r))
                    elif moved or (comp and sd):
                        o = 0
                        h["oppl"].append(s + ":x")
                    if o is not None:
                        opp["vs_chk"].setdefault(st, [0, 0])[o] += 1
            elif a == "CALL":
                h["us"].append("%s:C%d" % (s, prev["to_call"]))
            elif a == "FOLD":
                h["us"].append("%s:FOLD(to %d)" % (s, prev["to_call"]))
                opp["folds"] += 1
                opp["fold_lost"] += prev["to_call"]
            else:
                h["us"].append(s + ":" + a)

        # ---- completion ----
        if comp:
            n = int(comp.group(1))
            h = self._h(n)
            opp = self._os(h["opp"])
            g = re.search(r"Net chip change this hand:\s*([+-]?\d+)", res)
            if g:
                h["net"] = int(g.group(1))
            g = re.search(r"Total profit:\s*([+-]?\d+)", res)
            if g:
                self.total = int(g.group(1))
            g = re.search(r"Final pot:\s*(\d+)", res)
            if g:
                h["pot"] = int(g.group(1))
            g = re.search(r"Opponent's hand:\s*([^\n]*)", res)
            if g:
                h["sd"] = _cards(g.group(1))
            g = re.search(r"Board:\s*([^\n]*)", res)
            if g and _cards(g.group(1)):
                h["board"] = _cards(g.group(1))
            ua = act.get("arguments", {}) if isinstance(act, dict) else {}
            last_a = str(ua.get("action", "")).upper() if isinstance(ua, dict) else ""
            if sd:
                h["end"] = "showdown"
            elif last_a == "FOLD":
                h["end"] = "you folded"
            else:
                h["end"] = "opp folded"
            if h["net"] is not None:
                opp["hands"] += 1
                opp["net"] += h["net"]
                if h["end"] == "showdown" and h["sd"]:
                    opp["sd"].append((n, h["sd"], h["board"], " ".join(h["oppl"]), h["cards"], h["net"]))
                    opp["sd"] = opp["sd"][-8:]

        # ---- new decision ----
        if p:
            h = self._h(p["hand"])
            if p["pos"]:
                h["pos"], h["ip"] = p["pos"], p["ip"]
            if p["cards"]:
                h["cards"] = p["cards"]
            if p["board"]:
                h["board"] = p["board"]
            h["opp"] = p["opp"] or h["opp"]
            opp = self._os(h["opp"])
            st = p["street"]
            if st not in h["streets"]:
                h["streets"].add(st)
                s = ST.get(st, "?")
                if st == "PREFLOP" and not h["seen_pre"]:
                    h["seen_pre"] = True
                    if p["to_call"] > 0 and p["ip"]:
                        opp["btn_raise"] += 1
                        h["oppl"].append("P:raise")
                    elif p["to_call"] > 0 and not p["ip"]:
                        opp["bb_raise"] += 1
                        h["oppl"].append("P:raise")
                    elif p["to_call"] == 0 and not p["ip"]:
                        opp["bb_limp"] += 1
                        h["oppl"].append("P:limp")
                elif st != "PREFLOP" and p["ip"]:
                    if p["to_call"] > 0:
                        r = p["to_call"] / float(max(1, p["pot"] - p["to_call"]))
                        opp["bet_sz"].append(r)
                        opp["first"].setdefault(st, [0, 0])[1] += 1
                        h["oppl"].append("%s:bet%.1fp" % (s, r))
                    else:
                        opp["first"].setdefault(st, [0, 0])[0] += 1
                        h["oppl"].append(s + ":x")
            self.last = p
        elif comp:
            self.last = None

    @staticmethod
    def _rs(v):
        n = sum(v)
        if not n:
            return "none"
        return "fold %d/call %d/reraise %d (n=%d, fold %d%%)" % (v[0], v[1], v[2], n, 100 * v[0] // n)

    def brief(self):
        try:
            return self._brief()[:4000]
        except Exception:
            return "REGISTER: unavailable"

    def _brief(self):
        fin = [self.hands[n] for n in self.order if self.hands[n]["net"] is not None]
        tot = self.total if self.total is not None else sum(h["net"] for h in fin)
        cur = self.cur_opp
        o = self._os(cur)
        L = ["=== REGISTER (facts from this run) ==="]
        L.append("[1] RESULTS: hands done %d, total profit %+d | current opp: %s" % (len(fin), tot, cur))
        names = [k for k in self.opps if self.opps[k]["hands"]]
        if len(names) > 1:
            L.append("  per opp: " + "; ".join("%s %d hands net %+d" % (k, self.opps[k]["hands"], self.opps[k]["net"]) for k in names))
        big = [h for h in fin if h["pot"] >= 150]
        small = [h for h in fin if h["pot"] < 150 and h["pot"] > 0]
        if big:
            L.append("  pots>=150: %d hands, won %d, net %+d" % (len(big), sum(1 for h in big if h["net"] > 0), sum(h["net"] for h in big)))
        if small:
            L.append("  pots<150: %d hands, won %d, net %+d" % (len(small), sum(1 for h in small if h["net"] > 0), sum(h["net"] for h in small)))

        L.append("[2] %s RESPONSE TO YOUR BETS/RAISES" % cur)
        any_resp = False
        for st in ORDER:
            if st in o["resp"]:
                any_resp = True
                L.append("  %s: %s" % (st, self._rs(o["resp"][st])))
        for b in ("small(<.5p)", "med(.5-1p)", "big(>1p)"):
            agg = [0, 0, 0]
            for k, v in o["resp"].items():
                if k.endswith("|" + b):
                    agg = [agg[i] + v[i] for i in range(3)]
            if sum(agg):
                any_resp = True
                L.append("  size %s: %s" % (b, self._rs(agg)))
        if not any_resp:
            L.append("  none: you have made no postflop bets vs %s yet, so fold rate is unknown" % cur)

        L.append("[3] %s TENDENCIES" % cur)
        L.append("  preflop as BTN you faced: raise %d / limp %d" % (o["btn_raise"], o["btn_limp"]))
        L.append("  preflop as BB you faced: raise %d / limp %d" % (o["bb_raise"], o["bb_limp"]))
        for st in ORDER[1:]:
            if st in o["first"]:
                c, bt = o["first"][st]
                L.append("  %s opp first to act: check %d / bet %d" % (st, c, bt))
            if st in o["vs_chk"]:
                c, bt = o["vs_chk"][st]
                L.append("  %s after your check: check %d / bet %d" % (st, c, bt))
        if o["bet_sz"]:
            sz = o["bet_sz"]
            L.append("  opp bet size avg %.2f pot (n=%d)" % (sum(sz) / len(sz), len(sz)))

        L.append("[4] %s SHOWDOWNS (last 6): their hand | board | their line | your hand | your net" % cur)
        if not o["sd"]:
            L.append("  none yet")
        for n, sdc, bd, line, mine, net in o["sd"][-6:]:
            L.append("  #%d %s | %s | %s | %s | %+d" % (n, sdc, bd or "-", line or "-", mine, net))

        L.append("[5] YOUR FOLDS vs %s: %d folds, chips paid in to folds %d" % (cur, o["folds"], o["fold_lost"]))

        L.append("[6] LAST HANDS")
        recent = [n for n in self.order if self.hands[n]["opp"] == cur][-5:]
        if not recent:
            recent = self.order[-3:]
        for n in recent:
            h = self.hands[n]
            res = ("%+d %s" % (h["net"], h["end"])) if h["net"] is not None else "in progress"
            L.append("  #%d %s %s | you: %s | opp: %s | %s" % (
                n, "IP" if h["ip"] else "OOP", h["cards"] or "-", " ".join(h["us"]) or "-",
                " ".join(h["oppl"]) or "-", res))
        out = "\n".join(L)
        if len(out) > 4000:
            out = out[:3990] + "\n..."
        return out
