"""Aggregate benchmark JSON results and render charts (PNG) + a summary for the blog post.

Usage: python analyze.py --results results/ --out charts/
"""
import argparse, json, pathlib, statistics as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
import numpy as np

# --- palette (dataviz reference instance, light mode) ---
SURFACE = "#fcfcfb"; TEXT = "#0b0b0b"; TEXT2 = "#52514e"; GRID = "#e6e5e1"
COLORS = {"vanilla": "#2a78d6", "stratum": "#eb6834"}   # slot 1 blue, slot 2 orange - fixed per entity
LABELS = {"vanilla": "Vanilla 1.22.7", "stratum": "Stratum 1.22.7-stratum.2"}
ORDER = ["vanilla", "stratum"]

rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": GRID, "axes.labelcolor": TEXT2,
    "xtick.color": TEXT2, "ytick.color": TEXT2, "axes.titlecolor": TEXT, "axes.titleweight": "600",
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.titlelocation": "left", "axes.titlepad": 30, "legend.frameon": False,
})


TAG = ""

def load(results: pathlib.Path, exclude=()):
    runs = {}
    for f in sorted(results.glob("*-run*-*.json")):
        if any(f.name.startswith(x) for x in exclude):
            print("excluded", f.name); continue
        d = json.loads(f.read_text())
        if "error" in d:
            print("skipping", f.name, d["error"]); continue
        runs.setdefault((d["label"], d["mode"]), []).append(d)
    return runs


def med(xs): return float(st.median(xs)) if xs else float("nan")


def metric(runs, variant, mode, phase, key):
    return [r[phase][key] for r in runs.get((variant, mode), []) if phase in r]


def pct_change(base, new):
    return (new - base) / base * 100.0 if base else float("nan")


import textwrap
def subtitle(ax, text):
    ax.text(0, 1.02, "\n".join(textwrap.wrap(text + TAG, 118)), transform=ax.transAxes, fontsize=9, color=TEXT2, va="bottom", linespacing=1.3)


def style_axes(ax):
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    ax.spines["bottom"].set_color(GRID)


def bars_with_runs(ax, groups, variants, data, unit, title, subtitle=None, fmt="{:.1f}", lower_is_better=True):
    """groups: list of group labels; data[(variant, group)] -> list of per-run values. Bars = median, dots = runs."""
    n = len(variants); w = 0.8 / n; x = np.arange(len(groups))
    ymax = 0
    for i, v in enumerate(variants):
        meds = [med(data[(v, g)]) for g in groups]
        xs = x - 0.4 + w * (i + 0.5)
        ax.bar(xs, meds, width=w - 0.04, color=COLORS[v], label=LABELS[v], linewidth=0, zorder=2)
        for xi, g, m in zip(xs, groups, meds):
            pts = data[(v, g)]
            ax.scatter([xi] * len(pts), pts, s=14, color=SURFACE, edgecolor=TEXT2, linewidth=0.8, zorder=3)
            if not np.isnan(m):
                top = max([m] + pts)
                ax.text(xi, top, fmt.format(m), ha="center", va="bottom", fontsize=9.5, color=TEXT, zorder=4)
                ymax = max(ymax, top)
    ax.set_xticks(x, groups)
    ax.set_ylabel(unit)
    ax.set_ylim(0, ymax * 1.18 if ymax else 1)
    ax.set_title(title)
    if subtitle:
        globals()["subtitle"](ax, subtitle)
    ax.legend(loc="upper right", fontsize=9.5, ncol=1)
    style_axes(ax)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results"); ap.add_argument("--out", default="charts")
    ap.add_argument("--tag", default="", help="platform note appended to subtitles, e.g. 'Linux (WSL2)'")
    ap.add_argument("--exclude", default="", help="comma-separated run-file prefixes to drop, e.g. vanilla-run1")
    a = ap.parse_args()
    global TAG; TAG = (" " + a.tag) if a.tag else ""
    runs = load(pathlib.Path(a.results), [x for x in a.exclude.split(",") if x]); out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    V = [v for v in ORDER if any(k[0] == v for k in runs)]
    summary = {"runs": {f"{k[0]}/{k[1]}": len(v) for k, v in runs.items()}, "metrics": {}}

    def record(name, unit, per_variant, lower_is_better=True):
        m = {v: {"median": med(xs), "runs": xs} for v, xs in per_variant.items()}
        if "vanilla" in m and "stratum" in m:
            m["change_pct"] = pct_change(m["vanilla"]["median"], m["stratum"]["median"])
        m["unit"] = unit; m["lower_is_better"] = lower_is_better
        summary["metrics"][name] = m

    # ---------- 1. world generation + reload ----------
    cols = next((r["worldgen"]["columns"] for rs in runs.values() for r in rs if "worldgen" in r), 0)
    chunks = next((r["worldgen"]["chunks"] for rs in runs.values() for r in rs if "worldgen" in r), 0)
    data = {}
    for v in V:
        data[(v, "Generate new chunks")] = [x / 1000 for x in metric(runs, v, "gen", "worldgen", "wall_ms")]
        data[(v, "Reload from disk")] = [x / 1000 for x in metric(runs, v, "load", "worldgen", "wall_ms")]
    record("worldgen_wall_s", "s", {v: data[(v, "Generate new chunks")] for v in V})
    record("reload_wall_s", "s", {v: data[(v, "Reload from disk")] for v in V})
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    bars_with_runs(ax, ["Generate new chunks", "Reload from disk"], V, data, "seconds (lower is better)",
                   f"Time to load {cols:,} chunk columns ({chunks:,} chunks) around spawn",
                   "Bars are the median of all runs; dots are individual runs. Fresh world, fixed seed, no players.")
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "01_worldgen_time.png"); plt.close(fig)

    # columns per second
    data = {}
    for v in V:
        data[(v, "Generate")] = [cols / (x / 1000) for x in metric(runs, v, "gen", "worldgen", "wall_ms")]
        data[(v, "Reload")] = [cols / (x / 1000) for x in metric(runs, v, "load", "worldgen", "wall_ms")]
    record("worldgen_columns_per_s", "columns/s", {v: data[(v, "Generate")] for v in V}, lower_is_better=False)
    record("reload_columns_per_s", "columns/s", {v: data[(v, "Reload")] for v in V}, lower_is_better=False)

    # ---------- 2. worldgen CPU vs wall ----------
    data = {}
    for v in V:
        data[(v, "Wall-clock time")] = [x / 1000 for x in metric(runs, v, "gen", "worldgen", "wall_ms")]
        data[(v, "CPU time (all threads)")] = [x / 1000 for x in metric(runs, v, "gen", "worldgen", "cpu_ms")]
    record("worldgen_cpu_s", "s", {v: data[(v, "CPU time (all threads)")] for v in V})
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    bars_with_runs(ax, ["Wall-clock time", "CPU time (all threads)"], V, data, "seconds",
                   "World generation: wall-clock vs total CPU time",
                   "CPU time higher than wall time means work ran in parallel across cores.")
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "02_worldgen_cpu.png"); plt.close(fig)

    # ---------- 3. tick time under entity load ----------
    ents = next((r["entities"]["entities_spawned"] for rs in runs.values() for r in rs if "entities" in r), 0)
    for phase, fname, title, sub in [
        ("entities", "03_tick_entities.png", f"Main-thread tick time with {ents} active creatures",
         "Lower is better. Vintage Story budgets 33.3 ms per tick (30 TPS). No players online; creatures forced active."),
        ("idle", "04_tick_idle.png", f"Main-thread tick time at idle ({cols:,} chunk columns loaded, no players)",
         "Lower is better. Measured on every server tick for the whole phase."),
    ]:
        data = {}
        for v in V:
            for g, k in [("mean", "tick_mean_ms"), ("median", "tick_p50_ms"), ("p95", "tick_p95_ms"), ("p99", "tick_p99_ms")]:
                data[(v, g)] = metric(runs, v, "gen", phase, k)
                record(f"{phase}_tick_{g}_ms", "ms", {vv: metric(runs, vv, "gen", phase, k) for vv in V})
        fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
        bars_with_runs(ax, ["mean", "median", "p95", "p99"], V, data, "milliseconds per tick", title, sub, fmt="{:.2f}")
        fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / fname); plt.close(fig)

    # CPU per phase (idle + entities)
    data = {}
    for v in V:
        for g, ph in [("Idle", "idle"), (f"{ents} creatures", "entities")]:
            vals = [r[ph]["cpu_ms"] / r[ph]["wall_ms"] * 100 for r in runs.get((v, "gen"), []) if ph in r]
            data[(v, g)] = vals
            record(f"{ph}_cpu_pct_of_one_core", "%", {vv: [r[ph]["cpu_ms"] / r[ph]["wall_ms"] * 100 for r in runs.get((vv, "gen"), []) if ph in r] for vv in V})
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    bars_with_runs(ax, ["Idle", f"{ents} creatures"], V, data, "% of one CPU core (all threads)",
                   "Total process CPU usage by phase", "100% = one core fully busy. Includes worker threads, not just the main thread.", fmt="{:.0f}%")
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "05_cpu_usage.png"); plt.close(fig)

    # ---------- 4. tick timeline (first gen run, entity phase) ----------
    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=160)
    for v in V:
        rs = runs.get((v, "gen"), [])
        if not rs: continue
        r = rs[0]
        ENT_PHASE = 4 if any("settle" in r for rs in runs.values() for r in rs) else 3
        ser = [p for p in r["tick_series"] if p[2] == ENT_PHASE]
        if not ser: continue
        t0 = ser[0][0]
        t = np.array([p[0] - t0 for p in ser]) / 1000; y = np.array([p[1] for p in ser])
        ax.plot(t, y, color=COLORS[v], linewidth=0.6, alpha=0.25)
        # 1-second rolling mean
        k = max(1, int(len(y) / (t[-1] if t[-1] else 1)))
        ys = np.convolve(y, np.ones(k) / k, mode="same")
        ax.plot(t, ys, color=COLORS[v], linewidth=2, label=LABELS[v])
    ax.axhline(33.3, color=TEXT2, linewidth=0.8, linestyle=(0, (4, 3)))
    ax.text(0.2, 34, "33.3 ms tick budget (30 TPS)", fontsize=9, color=TEXT2, va="bottom")
    ax.set_ylim(0, 60)
    peaks = "; ".join(f"{LABELS[v].split()[0]} worst tick {max(p[1] for p in r['tick_series'] if p[2] == ENT_PHASE):.0f} ms" for v in V for r in runs.get((v, "gen"), [])[:1] if any(p[2] == ENT_PHASE for p in r["tick_series"]))
    ax.text(0.99, 0.97, "Y axis clipped at 60 ms. " + peaks + " (both at the moment of spawning).", transform=ax.transAxes, fontsize=8.5, color=TEXT2, ha="right", va="top")
    ax.set_xlabel("seconds since creatures were spawned"); ax.set_ylabel("ms per tick")
    ax.set_title(f"Tick time over the {ents}-creature phase (run 1)")
    subtitle(ax, "Faint line: every tick. Bold line: 1-second rolling average.")
    ax.legend(loc="center right", fontsize=9.5); style_axes(ax)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "06_tick_timeline.png"); plt.close(fig)

    # ---------- 5. memory ----------
    data = {}
    for v in V:
        data[(v, "Peak working set")] = [r["peak_working_set_mb"] for r in runs.get((v, "gen"), [])]
        data[(v, "Managed heap after worldgen")] = metric(runs, v, "gen", "worldgen", "managed_heap_end_mb")
        data[(v, "Managed heap after creatures")] = metric(runs, v, "gen", "entities", "managed_heap_end_mb")
    record("peak_working_set_mb", "MB", {v: data[(v, "Peak working set")] for v in V})
    record("heap_after_worldgen_mb", "MB", {v: data[(v, "Managed heap after worldgen")] for v in V})
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    bars_with_runs(ax, ["Peak working set", "Managed heap after worldgen", "Managed heap after creatures"], V, data, "MB",
                   "Memory use", "Working set is what the OS sees; managed heap is .NET's own allocation after the phase ended.", fmt="{:.0f}")
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "07_memory.png"); plt.close(fig)

    # ---------- 6. GC ----------
    data = {}
    for v in V:
        for g, ph in [("World generation", "worldgen"), ("Idle", "idle"), (f"{ents} creatures", "entities")]:
            record(f"{ph}_cpu_ms", "ms", {vv: metric(runs, vv, "gen", ph, "cpu_ms") for vv in V})
            data[(v, g)] = metric(runs, v, "gen", ph, "gc_pause_ms")
            record(f"{ph}_gc_pause_ms", "ms", {vv: metric(runs, vv, "gen", ph, "gc_pause_ms") for vv in V})
            record(f"{ph}_gc_gen0", "collections", {vv: metric(runs, vv, "gen", ph, "gc_gen0") for vv in V})
            record(f"{ph}_gc_gen2", "collections", {vv: metric(runs, vv, "gen", ph, "gc_gen2") for vv in V})
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    bars_with_runs(ax, ["World generation", "Idle", f"{ents} creatures"], V, data, "ms paused for GC during phase",
                   "Garbage-collector pause time", "Time all threads were stopped for .NET garbage collection. Lower is smoother.", fmt="{:.0f}")
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "08_gc_pause.png"); plt.close(fig)

    # ---------- 7. startup + TPS ----------
    data = {}
    for v in V:
        data[(v, "Fresh world")] = [r["startup_ms"] / 1000 for r in runs.get((v, "gen"), [])]
        data[(v, "Existing world")] = [r["startup_ms"] / 1000 for r in runs.get((v, "load"), [])]
    record("startup_fresh_s", "s", {v: data[(v, "Fresh world")] for v in V})
    record("startup_existing_s", "s", {v: data[(v, "Existing world")] for v in V})
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    bars_with_runs(ax, ["Fresh world", "Existing world"], V, data, "seconds",
                   "Server startup time", "From process launch until the server reports it is running the game.")
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "09_startup.png"); plt.close(fig)

    data = {}
    for v in V:
        for g, ph in [("Idle", "idle"), (f"{ents} creatures", "entities")]:
            data[(v, g)] = [r[ph]["tick_count"] / (r[ph]["wall_ms"] / 1000) for r in runs.get((v, "gen"), []) if ph in r]
            record(f"{ph}_tps", "ticks/s", {vv: [r[ph]["tick_count"] / (r[ph]["wall_ms"] / 1000) for r in runs.get((vv, "gen"), []) if ph in r] for vv in V}, lower_is_better=False)
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    bars_with_runs(ax, ["Idle", f"{ents} creatures"], V, data, "ticks per second (target 30)",
                   "Achieved tick rate", "The server targets 30 ticks per second; higher is better up to 30.")
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "10_tps.png"); plt.close(fig)

    # ---------- tick-time distribution (CDF), entity phase, all runs pooled ----------
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    for v in V:
        pooled = sorted(x for r in runs.get((v, "gen"), []) if "entities" in r for x in r["entities"]["ticks"])
        if not pooled: continue
        ys = np.arange(1, len(pooled) + 1) / len(pooled) * 100
        ax.plot(pooled, ys, color=COLORS[v], linewidth=2, label=LABELS[v])
    ax.set_xscale("log"); ax.set_xlim(0.3, 300)
    ax.set_xticks([0.5, 1, 2, 5, 10, 20, 33.3, 50, 100, 200], ["0.5", "1", "2", "5", "10", "20", "33", "50", "100", "200"])
    ax.axvline(33.3, color=TEXT2, linewidth=0.8, linestyle=(0, (4, 3)))
    ax.text(34, 5, "33.3 ms budget", fontsize=9, color=TEXT2, rotation=90, va="bottom")
    ax.set_xlabel("tick duration, ms (log scale)"); ax.set_ylabel("% of ticks at or below")
    ax.set_title(f"How long ticks take with {ents} active creatures: distribution")
    subtitle(ax, "Every tick from every run pooled. Further left is faster; a curve that reaches 100% sooner has a shorter tail.")
    ax.legend(loc="lower right", fontsize=9.5); style_axes(ax); ax.grid(True, axis="x", color=GRID, linewidth=0.5)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "11_tick_cdf.png"); plt.close(fig)

    # ---------- per-system breakdown (median per-tick ms across runs) ----------
    for ph, fname in [("idle", "12_systems_idle.png"), ("entities", "13_systems_entities.png")]:
        per = {}
        for v in V:
            acc = {}
            for r in runs.get((v, "gen"), []):
                sm = r.get(ph, {}).get("systems_ms")
                if not sm: continue
                n = r[ph]["tick_count"] or 1
                for k, x in sm.items(): acc.setdefault(k, []).append(x["total_ms"] / n)
            per[v] = {k: med(xs) for k, xs in acc.items()}
        if not any(per.values()): continue
        keys = [k for k in sorted({k for d in per.values() for k in d}, key=lambda k: -max(per[v].get(k, 0) for v in V)) if max(per[v].get(k, 0) for v in V) >= 0.01][:6]
        names = [k.replace("ServerSystem", "").replace(".OnServerTick", "").replace("EventManager.TriggerGameTick", "Mod + block tick listeners").replace("ServerMain.ProcessMainThreadTasks", "Main-thread task queue") for k in keys]
        fig, ax = plt.subplots(figsize=(9, 5.2), dpi=160)
        n = len(V); h = 0.8 / n; y = np.arange(len(keys))
        for i, v in enumerate(V):
            vals = [per[v].get(k, 0) for k in keys]
            ys = y - 0.4 + h * (i + 0.5)
            ax.barh(ys, vals, height=h - 0.04, color=COLORS[v], label=LABELS[v], linewidth=0, zorder=2)
            for yi, val in zip(ys, vals):
                ax.text(val, yi, f" {val:.2f}", va="center", fontsize=9, color=TEXT)
        ax.set_yticks(y, names); ax.invert_yaxis(); ax.set_xlabel("ms per tick on the main thread (median of runs)")
        ax.set_title("Where the tick time goes: " + ("idle" if ph == "idle" else f"{ents} active creatures"))
        subtitle(ax, "Measured around each server system's tick call. Systems not listed cost well under 0.01 ms.")
        ax.legend(loc="lower right", fontsize=9.5); ax.grid(True, axis="x", color=GRID, linewidth=0.8); ax.grid(False, axis="y"); ax.set_axisbelow(True); ax.tick_params(length=0)
        xmax = max(per[v].get(k, 0) for v in V for k in keys); ax.set_xlim(0, xmax * 1.25 if xmax else 1)
        fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / fname); plt.close(fig)
        for v in V:
            summary.setdefault("systems_per_tick_ms", {})[f"{v}/{ph}"] = {k: per[v].get(k, 0) for k in keys}

    # ---------- system breakdown if present ----------
    for v in V:
        for r in runs.get((v, "gen"), [])[:1]:
            for ph in ["idle", "entities"]:
                sm = r.get(ph, {}).get("systems_ms")
                if sm:
                    summary.setdefault("systems", {})[f"{v}/{ph}"] = {k: x for k, x in list(sm.items())[:12]}

    # ---------- load ladder (escalating entity count), if present ----------
    lad = {v: [r for r in runs.get((v, "gen"), []) if r.get("ladder")] for v in V}
    if any(lad.values()):
        targets = next(r["ladder_targets"] for rs in lad.values() for r in rs)
        def step_vals(v, i, f):
            return [f(r["ladder"][i]) for r in lad[v] if len(r["ladder"]) > i]
        steps = {v: max(len(r["ladder"]) for r in lad[v]) if lad[v] else 0 for v in V}
        tbl = ["| Creatures spawned | Vanilla mean / p99 tick | Vanilla TPS | Stratum mean / p99 tick | Stratum TPS |", "|---|---|---|---|---|"]
        for i, tgt in enumerate(targets):
            cells = []
            for v in ORDER:
                if v not in V or i >= steps[v]: cells += ["did not reach", ""]; continue
                mean = med(step_vals(v, i, lambda s: s["tick_mean_ms"])); p99 = med(step_vals(v, i, lambda s: s["tick_p99_ms"]))
                tps = med(step_vals(v, i, lambda s: s["tick_count"] / (s["wall_ms"] / 1000)))
                cells += [f"{mean:.1f} / {p99:.1f} ms", f"{tps:.1f}"]
                summary["metrics"][f"ladder_{tgt}_{v}"] = {"mean_ms": mean, "p99_ms": p99, "tps": tps, "entities_end": med(step_vals(v, i, lambda s: s["entities_end"])), "cpu_pct": med(step_vals(v, i, lambda s: s["cpu_ms"] / s["wall_ms"] * 100))}
            tbl.append(f"| {tgt:,} | " + " | ".join(cells) + " |")
        summary["ladder_table_md"] = "\n".join(tbl); print("\n".join(tbl))

        # 14: tick time vs creatures
        fig, ax = plt.subplots(figsize=(9, 5.2), dpi=160)
        for v in V:
            xs = [med(step_vals(v, i, lambda s: s["entities_end"])) for i in range(steps[v])]
            mean = [med(step_vals(v, i, lambda s: s["tick_mean_ms"])) for i in range(steps[v])]
            p99 = [med(step_vals(v, i, lambda s: s["tick_p99_ms"])) for i in range(steps[v])]
            ax.plot(xs, mean, color=COLORS[v], linewidth=2.2, marker="o", markersize=5, label=f"{LABELS[v]}: mean tick")
            ax.plot(xs, p99, color=COLORS[v], linewidth=1.6, linestyle=(0, (4, 2)), marker="o", markersize=4, markerfacecolor=SURFACE, label=f"{LABELS[v]}: p99 tick")
            for x_, m_ in zip(xs, mean): ax.text(x_, m_, f" {m_:.0f}", fontsize=8.5, color=TEXT, va="bottom" if v == "vanilla" else "top", ha="left")
        ax.axhline(33.3, color=TEXT2, linewidth=0.8, linestyle=(0, (4, 3))); ax.text(targets[0] * 0.5, 34.5, "33.3 ms tick budget (30 TPS)", fontsize=9, color=TEXT2, va="bottom")
        ax.set_xlabel("loaded entities (spawned creatures + wild animals)"); ax.set_ylabel("ms per tick (median of runs)")
        ax.set_title("Tick time as the creature count climbs")
        subtitle(ax, "Each step spawns more always-active creatures, then measures 30 s of ticks. Solid: mean; dashed: 99th percentile.")
        ax.legend(loc="upper left", fontsize=9); style_axes(ax); ax.set_xlim(left=0)
        fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "14_ladder_tick.png"); plt.close(fig)

        # 15: TPS vs creatures
        fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
        for v in V:
            xs = [med(step_vals(v, i, lambda s: s["entities_end"])) for i in range(steps[v])]
            tps = [med(step_vals(v, i, lambda s: s["tick_count"] / (s["wall_ms"] / 1000))) for i in range(steps[v])]
            ax.plot(xs, tps, color=COLORS[v], linewidth=2.2, marker="o", markersize=5, label=LABELS[v])
            for x_, t_ in zip(xs, tps): ax.text(x_, t_, f" {t_:.1f}", fontsize=8.5, color=TEXT, va="top" if v == "vanilla" else "bottom", ha="left")
        ax.axhline(30, color=TEXT2, linewidth=0.8, linestyle=(0, (4, 3))); ax.text(targets[0] * 0.5, 30.4, "30 TPS target", fontsize=9, color=TEXT2, va="bottom")
        ax.set_ylim(0, 33); ax.set_xlim(left=0)
        ax.set_xlabel("loaded entities (spawned creatures + wild animals)"); ax.set_ylabel("ticks per second achieved")
        ax.set_title("Tick rate as the creature count climbs")
        subtitle(ax, "The server targets 30 ticks per second. Once a tick takes longer than 33 ms, the rate falls and players feel it.")
        ax.legend(loc="lower left", fontsize=9.5); style_axes(ax)
        fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "15_ladder_tps.png"); plt.close(fig)

        # 16: timeline through the whole ladder (run 1 of each)
        fig, ax = plt.subplots(figsize=(10, 4.8), dpi=160)
        ymax = 0
        for v in V:
            r = lad[v][0]
            ser = [p for p in r["tick_series"] if p[2] >= 41]
            if not ser: continue
            t0 = ser[0][0]
            for si, st_ in enumerate(r["ladder"]):   # plot each step on its own so the spawning gaps stay gaps
                seg = [p for p in ser if p[2] == 40 + st_["step"]]
                if not seg: continue
                t = np.array([p[0] - t0 for p in seg]) / 1000; y = np.array([p[1] for p in seg])
                ax.plot(t, y, color=COLORS[v], linewidth=0.5, alpha=0.22)
                k = max(1, int(len(y) / ((t[-1] - t[0]) if t[-1] > t[0] else 1)))
                ys = np.convolve(y, np.ones(k) / k, mode="same"); ymax = max(ymax, float(np.percentile(ys, 99.5)))
                ax.plot(t, ys, color=COLORS[v], linewidth=2, label=LABELS[v] if si == 0 else None)
            if v == V[0]:
                for st_ in r["ladder"]:
                    tt = [p[0] - t0 for p in r["tick_series"] if p[2] == 40 + st_["step"]]
                    if tt: ax.axvline(tt[0] / 1000, color=GRID, linewidth=0.8); ax.text(tt[0] / 1000 + 0.3, 0.98, f"{st_['entities_target']:,}", transform=ax.get_xaxis_transform(), fontsize=8, color=TEXT2, va="top")
        ax.axhline(33.3, color=TEXT2, linewidth=0.8, linestyle=(0, (4, 3))); ax.text(0.5, 34.5, "33.3 ms tick budget", fontsize=9, color=TEXT2, va="bottom")
        ax.set_ylim(0, max(60, ymax * 1.3)); ax.set_xlabel("seconds since the first creature step"); ax.set_ylabel("ms per tick")
        ax.set_title("Tick time through the load ladder (run 1)")
        subtitle(ax, "Faint: every tick. Bold: 1-second rolling average. Vertical lines mark each spawn step (label = creatures spawned so far). Gaps are the spawning itself, which is not measured.")
        ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.93), fontsize=9.5); style_axes(ax)
        fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out / "16_ladder_timeline.png"); plt.close(fig)

    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    # markdown table
    lines = ["| Metric | Vanilla | Stratum | Change |", "|---|---|---|---|"]
    for name, m in summary["metrics"].items():
        if "vanilla" not in m or "stratum" not in m: continue
        a_, b_ = m["vanilla"]["median"], m["stratum"]["median"]
        if np.isnan(a_) or np.isnan(b_): continue
        ch = m.get("change_pct", float("nan"))
        better = (ch < 0) == m["lower_is_better"]
        lines.append(f"| {name} | {a_:,.2f} {m['unit']} | {b_:,.2f} {m['unit']} | {ch:+.1f}% {'(better)' if better else '(worse)'} |")
    (out / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
