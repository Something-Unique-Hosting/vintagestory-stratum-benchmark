# Vintage Story server benchmark: Stratum vs vanilla

A repeatable, player-free benchmark for the Vintage Story dedicated server. It was built to compare the
[Stratum](https://github.com/StratumServer/Stratum) server fork against the official server, but it works
for any two server builds you want to put side by side.

It measures, for each server:

- **World generation**: wall-clock and CPU time to generate a square of chunk columns around spawn, and to reload them from disk.
- **Tick time**: every main-thread tick, at idle and under creature load (mean, median, p95, p99, worst), plus achieved TPS.
- **Load ladder** (optional): spawns always-active creatures in steps (for example 1,000 → 10,000) and measures each step, to find where the tick rate breaks.
- **Memory and GC**: working set, managed heap, GC counts and pause time per phase.
- **Per-system timing**: how much of each tick goes to entity simulation, block-tick listeners, and so on.

The numbers behind our blog post, *Stratum vs Vanilla Vintage Story Server: We Benchmarked It (1.22.7)*, are in
[`results/`](results/) and the charts in [`charts/`](charts/). They were produced inside a 4-core, 6 GB game server
on one of our nodes. Headline: world generation 21% faster, 99th-percentile tick 40% shorter with 1,500 entities,
about twice the creature count before the tick rate slips, at the cost of about 900 MB more memory.

## How it works

Everything is driven by a small server-side mod, `stratumbench`, so no players and no client are needed:

1. **Startup** is timed from process launch to the `RunGame` phase on a fresh world with a fixed seed.
2. **Worldgen**: the mod requests a `(2r+1)²` square of chunk columns through the normal priority chunk-loading API and waits until every column reports loaded.
3. **Settle**: a pause for background work (neighbour columns, lighting, first save). Not reported.
4. **Idle**: nothing happens; every tick is measured.
5. **Creatures**: a fixed number of creatures (wolves, drifters, deer, foxes, hares) are spawned, flagged always-active and stripped of their timed despawn, then ticks are measured for N seconds. In ladder mode this becomes a series of spawn steps.
6. The mod writes one JSON file and shuts the server down.
7. **Reload** (optional second run): the server restarts on the world it just made and the same columns are loaded from disk.

Ticks are timed with a Harmony hook around `ServerMain.Process` / `ProcessMain`, which excludes the sleep between
ticks. A second tiny component, `NoNetHook`, is a .NET startup hook that stops the server opening its listen sockets
(there are no clients), so the test is hermetic and runs where listening is blocked. It is applied to both servers.

## Layout

```
mod/          the benchmark mod (BenchModSystem.cs, StratumBench.csproj, modinfo.json)
nonet/        the no-listen startup hook
harness/      run_bench.py (runs both servers alternately), analyze.py (charts + summary), config templates
container/    bash runner for a Pterodactyl/Calagopus-style game-server container, bundle builder, heap-dump helper
results/      raw per-run JSON and server logs from our runs (ny8-panel = 500 creatures, ny8-panel-ladder = load ladder)
charts/       the PNG charts and summary tables generated from results/
```

## Prerequisites

- **.NET 10 SDK** to build the mod and the hook (the servers themselves need the .NET 10 runtime).
- **Python 3.10+** with `matplotlib` and `numpy` for the harness and charts (`pip install matplotlib numpy`).
- The **official Vintage Story dedicated server** for the version you want to test (we used 1.22.7, `vs_server_linux-x64_1.22.7.tar.gz`). It runs on Windows too via `VintagestoryServer.exe`.
- A **Stratum release** for the same game version (we used `v1.22.7-stratum.2`).

## Set up the two servers

```bash
mkdir -p servers/vanilla servers/stratum
# vanilla: extract the official server archive
tar xzf vs_server_linux-x64_1.22.7.tar.gz -C servers/vanilla

# stratum: extract the Stratum release, then let its launcher download + patch the matching official server
tar xzf stratum-v1.22.7-stratum.2-linux-x64.tar.gz -C servers/stratum
cd servers/stratum && DOTNET_ROOT=$HOME/.dotnet ./StratumServer --stratum-prepare-only && cd ../..
```

After `--stratum-prepare-only`, `servers/stratum` holds a patched `VintagestoryServer.dll` and the harness launches
it directly (the launcher is only needed to bootstrap). Both servers should be the **same official build**; the only
difference is then Stratum's patch set. The launcher keeps the pristine download in `.vanilla-cache/extract`, which
is a convenient source for `servers/vanilla`.

## Build the mod and the hook

```bash
dotnet build mod   -c Release -p:VS_SERVER=$PWD/servers/vanilla -p:OutputPath=$PWD/build/stratumbench/
dotnet build nonet -c Release -p:VS_SERVER=$PWD/servers/vanilla -p:OutputPath=$PWD/build/nonet/
```

`VS_SERVER` just points at any server folder with `VintagestoryAPI.dll` and `Lib/0Harmony.dll` to compile against.
`build/stratumbench/` ends up with `StratumBench.dll` and `modinfo.json`; `build/nonet/` with `NoNetHook.dll`.

## Run it (Linux, Windows, WSL)

```bash
export DOTNET_ROOT=$HOME/.dotnet        # wherever the .NET 10 runtime lives (Linux only)
python3 harness/run_bench.py --runs 4 --radius 20 --settle 20 --idle 20 --entities 500 --entity-sec 45 \
        --modes gen,load --systiming --out results/mine --data benchdata
python3 harness/analyze.py --results results/mine --out charts/mine --tag "my machine"
```

What the options mean:

| Option | Default | Meaning |
|---|---|---|
| `--runs` | 3 | Full runs per server. Variants alternate (vanilla, stratum, stratum, vanilla, ...) so drift hits both equally. |
| `--radius` | 16 | Chunk-column radius around spawn. 20 = 41×41 = 1,681 columns (13,448 chunks). |
| `--settle` / `--idle` | 20 / 20 | Seconds for the settle and idle phases. |
| `--entities` / `--entity-sec` | 300 / 45 | Creatures to spawn and how long to measure them. 0 skips the phase. |
| `--ladder` | off | Load ladder, cumulative targets, e.g. `--ladder 1000,2000,3000,4000,6000,8000,10000`. Replaces the fixed creature phase on gen runs. |
| `--step-sec` | 30 | Seconds measured per ladder step. |
| `--modes` | gen,load | `gen` = fresh world; `load` = restart on that world and reload from disk. |
| `--variants` | vanilla,stratum | Which servers to run. |
| `--systiming` | off | Wrap every server system's tick to attribute time (small overhead, same on both). |
| `--servers` / `--moddir` / `--hook` | repo defaults | Where the server folders, the built mod folder and `NoNetHook.dll` are. |
| `--out` / `--data` | results/, benchdata/ | Where JSON results and the per-run data folders go. |

Each run gets a fresh data folder, an identical `serverconfig.json` (built from `harness/serverconfig.template.json`,
seed `20261030`, "survive and build", no advertising) and, for Stratum, a `stratum.json` that only switches off the
update checker, the usage report and scheduled backups. Everything else is left at defaults.

`analyze.py` writes the charts (`01_worldgen_time.png` ... `16_ladder_timeline.png`), `summary.json` and
`summary.md` (median of runs, per-run values, percentage change). Use `--exclude vanilla-run1` to drop a bad run.

A full 4-run pass with `--radius 20 --entities 500` takes about 20 minutes on a desktop and about 30 minutes in a
4-core container. The ladder to 10,000 creatures adds about 5 minutes per run.

## Run it inside a game-server container (Pterodactyl, Pelican, Calagopus)

This is how we ran it: as the startup command of an ordinary server on the `ghcr.io/parkervcp/yolks:dotnet_10`
image, so the test goes through the same container, CPU quota, memory limit and filesystem a customer gets.

1. Build the bundle on a machine that has both servers and the built mod/hook:
   ```bash
   container/make_bundle.sh servers/vanilla servers/stratum build bench-bundle.tgz
   ```
   The tarball holds `servers/{vanilla,stratum}` and `bench/` (mod, hook, runner, config templates), about 125 MB.
2. Create a server on the Vintage Story egg (any limits you want to test; we used 4 cores / 6 GB), skip the installer,
   upload `bench-bundle.tgz` through the panel's file manager and extract it in the server's root.
3. Set the startup command to:
   ```
   RUNS=4 bash /home/container/bench/run_in_container.sh
   ```
   or, for the load ladder:
   ```
   LADDER=1000,2000,3000,4000,6000,8000,10000 STEP_SEC=30 RUNS=3 MODES=gen bash /home/container/bench/run_in_container.sh
   ```
   Other knobs: `RADIUS IDLE SETTLE ENTITIES ENTITY_SEC VARIANTS MODES TIMEOUT`.
4. Start the server. Results land in `/home/container/results/` (one JSON per run, plus the server logs and an `env.txt`
   recording the cgroup limits). The runner prints `BENCH DONE` and then **holds the container open**; stop the
   server from the panel and download `results/`. Wings restarts a server on any exit, which would rerun the
   benchmark and overwrite the results, so the runner never exits on its own.
5. Run `harness/analyze.py` on the downloaded folder.

Two things we learned the hard way, already handled in the runner: Wings attaches a TTY, so the game must be
started with `timeout --foreground` and stdin from `/dev/null` or it stops on `SIGTTIN` at zero CPU; and the image
has no NuGet access, so `container/gcdump_in_container.sh` expects `dotnet-gcdump` to be shipped as files
(`tools/gcdump/`) if you want heap dumps.

## Environment variables read by the mod

The harness sets these; you only need them if you launch the server by hand.

| Variable | Meaning |
|---|---|
| `BENCH_LABEL`, `BENCH_MODE` | Name written into the result; `gen` or `load`. |
| `BENCH_OUT` | Path of the JSON result file. |
| `BENCH_RADIUS`, `BENCH_SETTLE_SEC`, `BENCH_IDLE_SEC` | Worldgen radius and phase lengths. |
| `BENCH_ENTITIES`, `BENCH_ENTITY_SEC` | Fixed creature phase. |
| `BENCH_LADDER`, `BENCH_STEP_SEC`, `BENCH_SPAWN_PER_TICK`, `BENCH_LADDER_STOP_MS` | Ladder targets (cumulative), seconds per step, spawn slice per 250 ms tick, early-stop threshold on mean tick (default 150 ms). |
| `BENCH_SYSTIMING` | `1` enables per-system tick timing. |
| `BENCH_NONET` | `1` makes `NoNetHook` disable listen sockets (needs `DOTNET_STARTUP_HOOKS=<path>/NoNetHook.dll`). |

## Result file

One JSON per run: `startup_ms`, per-phase blocks (`worldgen`, `settle`, `idle`, `entities` or `ladder[]`) with
`wall_ms`, `cpu_ms`, `gc_pause_ms`, `gc_gen0/1/2`, tick statistics, memory at the end of the phase and
`systems_ms`; `samples` every 250 ms (working set, managed heap, threads, loaded entities, CPU);
and `tick_series` with every tick as `[t_ms, tick_ms, phase]` (ladder steps are tagged `40 + step`).

## Caveats

- No real players are connected. Stratum's distance-based entity throttling and hard despawn only act when players
  are online, so this benchmark does not credit them. Network behaviour is not measured.
- The creature phase forces creatures active, which is harsher than a live server where most creatures idle.
- A mod that Harmony-patches the worldgen code paths can make Stratum fall back to the vanilla terrain pass. Run with
  no mods other than `stratumbench`, as the harness does, when comparing servers.
- Stratum's first tick after a very large batch of chunk requests is about one second; it happens once and is an
  artefact of requesting 1,681 columns in a single tick.

## License

MIT. Vintage Story and Stratum are not included; download them from their own sites. Something Unique Hosting is not
affiliated with the Stratum project.
