import re

_PEAK_RE = re.compile(
    r"freq:\s*(-?[\d.]+)\s*MHz\s*\|\s*power:\s*(-?[\d.]+)\s*dBm\s*\|\s*width:\s*(-?[\d.]+)\s*MHz",
    re.I)
_BAND_RE = re.compile(r"Band:\s*(-?[\d.]+)\s*-\s*(-?[\d.]+)\s*MHz", re.I)
_NOISE_RE = re.compile(r"estimated_noise_floor_dbm:\s*(-?[\d.]+)", re.I)
_SCAN_RE = re.compile(r"Scan\s+(\d+)\s*/\s*(\d+)", re.I)
_SCORE_RE = re.compile(r"(iou|reward|score|accuracy)[^0-9\-]{0,20}(-?[\d.]+)", re.I)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2:
        return s[n // 2]
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


def _union_len(ivs):
    ivs = sorted(i for i in ivs if i[1] > i[0])
    tot = 0.0
    cur = None
    for a, b in ivs:
        if cur is None:
            cur = [a, b]
        elif a <= cur[1]:
            cur[1] = max(cur[1], b)
        else:
            tot += cur[1] - cur[0]
            cur = [a, b]
    if cur:
        tot += cur[1] - cur[0]
    return tot


class Register:
    def __init__(self) -> None:
        self.bands = {}          # band key -> {"clusters": [...], "scans": int, "lo","hi"}
        self.cur_band = None
        self.cur_peaks = []      # list of (f,p,w,cluster_index)
        self.cur_scan_label = ""
        self.noise = None
        self.reports = []        # dicts: scan, n_tx, occ_mhz, missed, score, fb
        self.total_scans = 0
        self.last_seen_sig = None

    # ---------- parsing ----------
    def _band(self, key):
        b = self.bands.get(key)
        if b is None:
            b = {"clusters": [], "scans": 0, "lo": None, "hi": None}
            self.bands[key] = b
        return b

    def _match(self, clusters, f, w):
        best, bd = None, None
        for i, c in enumerate(clusters):
            cf = _median(c["f"])
            cw = _median(c["w"])
            tol = max(1.0, 0.5 * (w + cw))
            d = abs(f - cf)
            if d <= tol and (bd is None or d < bd):
                best, bd = i, d
        return best

    def _ingest_scan(self, seen):
        bm = _BAND_RE.search(seen)
        key = "%s-%s" % (bm.group(1), bm.group(2)) if bm else "unknown"
        b = self._band(key)
        if bm:
            try:
                b["lo"], b["hi"] = float(bm.group(1)), float(bm.group(2))
            except Exception:
                pass
        self.cur_band = key
        nm = _NOISE_RE.search(seen)
        self.noise = float(nm.group(1)) if nm else None
        sm = _SCAN_RE.search(seen)
        self.cur_scan_label = sm.group(0) if sm else ""
        b["scans"] += 1
        self.total_scans += 1
        sidx = b["scans"]
        self.cur_peaks = []
        for m in _PEAK_RE.finditer(seen):
            try:
                f, p, w = float(m.group(1)), float(m.group(2)), float(m.group(3))
            except Exception:
                continue
            ci = self._match(b["clusters"], f, w)
            if ci is None:
                b["clusters"].append({"f": [], "w": [], "p": [], "scans": [], "snr": []})
                ci = len(b["clusters"]) - 1
            c = b["clusters"][ci]
            c["f"].append(f); c["w"].append(w); c["p"].append(p)
            if sidx not in c["scans"]:
                c["scans"].append(sidx)
            if self.noise is not None:
                c["snr"].append(p - self.noise)
            self.cur_peaks.append((f, p, w, ci))

    def _ingest_action(self, action, result):
        args = action.get("arguments") or {}
        txs = args.get("transmitters") or []
        ivs = []
        for t in txs:
            try:
                cf = float(t.get("center_freq")); bw = float(t.get("bandwidth"))
                ivs.append((cf - bw / 2, cf + bw / 2))
            except Exception:
                pass
        missed = []
        b = self.bands.get(self.cur_band)
        if b:
            for i, c in enumerate(b["clusters"]):
                cf = _median(c["f"])
                if not any(a <= cf <= z for a, z in ivs):
                    missed.append(i)
        score = None
        sm = _SCORE_RE.search(result or "")
        if sm:
            try:
                score = float(sm.group(2))
            except Exception:
                score = None
        fb = " ".join((result or "").split())[:120]
        self.reports.append({"scan": self.total_scans, "n": len(ivs),
                             "occ": _union_len(ivs), "missed": missed,
                             "score": score, "fb": fb})

    def observe(self, step: dict) -> None:
        try:
            action = step.get("action")
            result = step.get("result") or ""
            seen = step.get("seen") or ""
            if action and isinstance(action, dict):
                try:
                    self._ingest_action(action, result)
                except Exception:
                    pass
            if seen and _PEAK_RE.search(seen) or _BAND_RE.search(seen or ""):
                sig = hash(seen)
                if sig != self.last_seen_sig:
                    self.last_seen_sig = sig
                    self._ingest_scan(seen)
        except Exception:
            pass

    # ---------- output ----------
    def brief(self) -> str:
        try:
            return self._brief()[:4000]
        except Exception:
            return "Register unavailable this step."

    def _brief(self):
        out = []
        b = self.bands.get(self.cur_band)
        n = b["scans"] if b else 0
        out.append("== REGISTER (facts from this run) ==")
        out.append("[1] Status: band %s MHz | scans of this band seen: %d | total scans: %d | %s"
                   % (self.cur_band, n, self.total_scans, self.cur_scan_label))
        if self.noise is not None:
            out.append("    noise floor now: %.1f dBm" % self.noise)

        out.append("[2] Past reports -> feedback (last 6):")
        if not self.reports:
            out.append("    none yet")
        for r in self.reports[-6:]:
            sc = "score=%.3f" % r["score"] if r["score"] is not None else "no score shown"
            out.append("    scan#%d: %d tx, %.1f MHz occupied, omitted %d known clusters, %s | '%s'"
                       % (r["scan"], r["n"], r["occ"], len(r["missed"]), sc, r["fb"]))

        if not b:
            return "\n".join(out)
        cl = b["clusters"]
        cur_ids = set(ci for _, _, _, ci in self.cur_peaks)
        order = sorted(range(len(cl)), key=lambda i: _median(cl[i]["f"]))
        rep = [i for i in order if len(cl[i]["scans"]) >= 2 or n <= 1 or i in cur_ids]
        rare = [i for i in order if i not in rep]
        out.append("[3] Transmitter clusters across scans of this band (by freq):")
        out.append("    id | center | med width | span seen (lo-hi) | seen k/N scans | med power | SNR | active now")
        lines = 0
        for i in rep:
            c = cl[i]
            cf = _median(c["f"]); cw = _median(c["w"])
            lo = min(f - w / 2 for f, w in zip(c["f"], c["w"]))
            hi = max(f + w / 2 for f, w in zip(c["f"], c["w"]))
            snr = ("%.1f" % _median(c["snr"])) if c["snr"] else "?"
            out.append("    C%d | %.2f | %.2f | %.2f-%.2f | %d/%d | %.1f | %s | %s"
                       % (i, cf, cw, lo, hi, len(c["scans"]), n, _median(c["p"]), snr,
                          "YES" if i in cur_ids else "no"))
            lines += 1
            if lines >= 40:
                out.append("    ... (%d more)" % (len(rep) - lines))
                break
        if rep:
            ivs = []
            for i in rep:
                cf = _median(cl[i]["f"]); cw = _median(cl[i]["w"])
                ivs.append((cf - cw / 2, cf + cw / 2))
            occ = _union_len(ivs)
            tot = (b["hi"] - b["lo"]) if b["lo"] is not None else None
            out.append("    clusters above cover %.1f MHz (median widths)%s"
                       % (occ, (" of %.1f MHz band" % tot) if tot else ""))

        out.append("[4] One-off peaks (seen in only 1 of %d scans, not active now):" % n)
        if not rare:
            out.append("    none")
        else:
            s = ", ".join("%.1f(w%.1f,%.0fdBm,scan%d)" % (cl[i]["f"][0], cl[i]["w"][0],
                                                          cl[i]["p"][0], cl[i]["scans"][0])
                          for i in rare[:25])
            out.append("    " + s)

        out.append("[5] Current scan peaks -> cluster:")
        if not self.cur_peaks:
            out.append("    no peaks detected this scan")
        else:
            out.append("    " + ", ".join("%.1f->C%d" % (f, ci) for f, _, _, ci in self.cur_peaks[:40]))
        if self.reports and self.reports[-1]["missed"]:
            m = [i for i in self.reports[-1]["missed"] if i in rep][:20]
            if m:
                out.append("    Clusters not covered by your last report: " + ", ".join("C%d" % i for i in m))
        return "\n".join(out)
