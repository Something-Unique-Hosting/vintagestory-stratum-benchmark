"""Benchmark harness: runs vanilla Vintage Story server vs Stratum with the StratumBench mod.

Each run gets a fresh data folder, a fixed world seed, no network advertising, and the mod
drives the whole benchmark then shuts the server down. Variants are interleaved so thermal
drift and background noise affect both sides equally.

Usage: python run_bench.py --runs 3 --radius 16 --entities 300 --out results/
"""
import argparse, json, os, shutil, subprocess, sys, time, pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
if sys.platform == "win32":
    SERVERS = {
        "vanilla": {"dir": ROOT / "servers" / "vanilla", "cmd": ["VintagestoryServer.exe"]},
        "stratum": {"dir": ROOT / "servers" / "stratum", "cmd": ["VintagestoryServer.exe"]},  # patched files already in place after prepare; launcher only bootstraps
    }
else:
    DOTNET = os.environ.get("DOTNET_ROOT", str(pathlib.Path.home() / ".dotnet")) + "/dotnet"
    SERVERS = {
        "vanilla": {"dir": ROOT / "servers" / "vanilla", "cmd": [DOTNET, "VintagestoryServer.dll"]},
        "stratum": {"dir": ROOT / "servers" / "stratum", "cmd": [DOTNET, "VintagestoryServer.dll"]},
    }
MOD_DIR = ROOT / "build"          # contains stratumbench/ folder mod (overridden by --moddir)
HOOK_DLL = ROOT / "build" / "nonet" / "NoNetHook.dll"   # overridden by --hook

SEED = "20261030"


def write_server_config(datapath: pathlib.Path, world_name: str):
    cfg_path = datapath / "serverconfig.json"
    cfg = json.loads((HERE / "serverconfig.template.json").read_text())
    cfg["ServerName"] = "SUH Benchmark"
    cfg["AdvertiseServer"] = False
    cfg["Upnp"] = False
    cfg["Port"] = 42777
    cfg["VerifyPlayerAuth"] = False
    cfg["PassTimeWhenEmpty"] = True
    cfg["ModPaths"] = ["Mods", str(MOD_DIR)]
    cfg["WorldConfig"]["Seed"] = SEED
    cfg["WorldConfig"]["WorldName"] = world_name
    cfg["WorldConfig"]["SaveFileLocation"] = str(datapath / "Saves" / "bench.vcdbs")
    cfg["WorldConfig"]["PlayStyle"] = "surviveandbuild"
    cfg["WorldConfig"]["PlayStyleLangCode"] = "surviveandbuild-bands"
    cfg["WorldConfig"]["WorldType"] = "standard"
    (datapath / "Saves").mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2))


def write_stratum_config(datapath: pathlib.Path):
    # Keep Stratum's performance defaults untouched; only silence the things that phone home
    # or do scheduled disk work during a benchmark.
    stratum = {
        "ConfigVersion": 3,
        "UpdateChecker": {"Enabled": False, "CheckOnStartup": False},
        "ServerStats": {"Enabled": False},
        "Backup": {"Enabled": False},
    }
    (datapath / "stratum.json").write_text(json.dumps(stratum, indent=2))


def run_one(variant: str, run_idx: int, mode: str, datapath: pathlib.Path, out_json: pathlib.Path, args) -> dict:
    s = SERVERS[variant]
    if mode == "gen":
        if datapath.exists():
            shutil.rmtree(datapath)
        datapath.mkdir(parents=True)
        write_server_config(datapath, f"bench-{variant}-{run_idx}")
        if variant == "stratum":
            write_stratum_config(datapath)
    env = os.environ.copy()
    env.update({
        "BENCH_LABEL": variant, "BENCH_MODE": mode, "BENCH_OUT": str(out_json),
        "BENCH_RADIUS": str(args.radius), "BENCH_IDLE_SEC": str(args.idle if mode == "gen" else 10),
        # load mode only measures chunk loading from disk; the gen run's entities are still in the save
        "BENCH_ENTITIES": str(args.entities if mode == "gen" else 0), "BENCH_ENTITY_SEC": str(args.entity_sec),
        "DOTNET_TieredPGO": "1",
        "BENCH_NONET": "1", "BENCH_SYSTIMING": "1" if args.systiming else "0", "BENCH_SETTLE_SEC": str(args.settle),
        "DOTNET_STARTUP_HOOKS": str(HOOK_DLL),
    })
    if args.ladder:
        env.update({"BENCH_LADDER": args.ladder if mode == "gen" else "", "BENCH_STEP_SEC": str(args.step_sec)})
    cmd = [*([str(s["dir"] / c) if c.endswith(".exe") else c for c in s["cmd"]]), "--dataPath", str(datapath)]
    log = out_json.with_suffix(".stdout.log")
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] {variant} run {run_idx} mode={mode} -> {out_json.name}", flush=True)
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        p = subprocess.Popen(cmd, cwd=str(s["dir"]), env=env, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            rc = p.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            p.kill(); rc = -9
            print("   TIMEOUT, killed", flush=True)
    dt = time.time() - t0
    ok = out_json.exists()
    print(f"   exit={rc} elapsed={dt:0.0f}s result={'ok' if ok else 'MISSING'}", flush=True)
    # keep the server's own log next to the result
    main_log = datapath / "Logs" / "server-main.log"
    if main_log.exists():
        shutil.copy(main_log, out_json.with_suffix(".server-main.log"))
    return {"variant": variant, "run": run_idx, "mode": mode, "rc": rc, "elapsed_s": dt, "ok": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--radius", type=int, default=16)
    ap.add_argument("--idle", type=int, default=20)
    ap.add_argument("--entities", type=int, default=300)
    ap.add_argument("--entity-sec", type=int, default=45)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--variants", default="vanilla,stratum")
    ap.add_argument("--modes", default="gen,load", help="gen = fresh world; load = restart on the world the gen run created")
    ap.add_argument("--data", default=str(ROOT / "benchdata"))
    ap.add_argument("--moddir", default=None)
    ap.add_argument("--systiming", action="store_true")
    ap.add_argument("--settle", type=int, default=20)
    ap.add_argument("--hook", default=None, help="path to NoNetHook.dll (default build/nonet/NoNetHook.dll)")
    ap.add_argument("--servers", default=None, help="folder holding vanilla/ and stratum/ (default <repo>/servers)")
    ap.add_argument("--ladder", default="", help="load ladder: cumulative creature targets, e.g. 1000,2000,4000,8000 (gen runs only)")
    ap.add_argument("--step-sec", type=int, default=30, help="seconds measured per ladder step")
    a = ap.parse_args()
    global MOD_DIR, HOOK_DLL
    if a.moddir: MOD_DIR = pathlib.Path(a.moddir)
    if a.hook: HOOK_DLL = pathlib.Path(a.hook)
    if a.servers:
        for v in SERVERS: SERVERS[v]["dir"] = pathlib.Path(a.servers) / v
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    data = pathlib.Path(a.data); data.mkdir(parents=True, exist_ok=True)
    variants = a.variants.split(","); modes = a.modes.split(",")
    summary = []
    for r in range(1, a.runs + 1):
        order = variants if r % 2 == 1 else list(reversed(variants))  # alternate who goes first
        for v in order:
            dp = data / f"{v}-{r}"
            for m in modes:
                res = run_one(v, r, m, dp, out / f"{v}-run{r}-{m}.json", a)
                summary.append(res)
                (out / "runs.json").write_text(json.dumps(summary, indent=2))
    print("done")


if __name__ == "__main__":
    main()
