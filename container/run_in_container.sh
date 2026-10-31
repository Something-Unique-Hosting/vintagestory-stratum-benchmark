#!/bin/bash
# Stratum vs vanilla benchmark, container edition. Runs as the panel server's startup command inside
# the dotnet_10 yolk image (no python/jq needed). Mirrors run_bench.py: alternating vanilla/Stratum,
# fresh world per gen run, fixed seed, then a load run on the same world. Results land in /home/container/results.
#   env knobs (set as egg variables or just edit here): RUNS RADIUS IDLE SETTLE ENTITIES ENTITY_SEC VARIANTS MODES
set -u
H=/home/container
B=$H/bench
RUNS=${RUNS:-4}; RADIUS=${RADIUS:-20}; IDLE=${IDLE:-20}; SETTLE=${SETTLE:-20}
ENTITIES=${ENTITIES:-500}; ENTITY_SEC=${ENTITY_SEC:-45}; VARIANTS=${VARIANTS:-vanilla,stratum}; MODES=${MODES:-gen,load}
TIMEOUT=${TIMEOUT:-1500}
# Load ladder (optional): LADDER="1000,2000,4000,6000,8000,10000" STEP_SEC=30 replaces the fixed creature phase.
LADDER=${LADDER:-}; STEP_SEC=${STEP_SEC:-30}; SPAWN_PER_TICK=${SPAWN_PER_TICK:-250}; LADDER_STOP_MS=${LADDER_STOP_MS:-150}
OUT=$H/results; DATA=$H/benchdata
mkdir -p "$OUT" "$DATA"

{
  echo "date: $(date -u +%FT%TZ)"; echo "host: $(hostname)"; echo "nproc: $(nproc)"
  echo "cgroup memory.max: $(cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null)"
  echo "cgroup cpu.max: $(cat /sys/fs/cgroup/cpu.max 2>/dev/null)"
  echo "dotnet: $(dotnet --version 2>/dev/null)"; grep -m1 'model name' /proc/cpuinfo; grep -E 'MemTotal|MemAvailable' /proc/meminfo
  echo "SERVER_MEMORY=${SERVER_MEMORY:-?} P_SERVER_UUID=${P_SERVER_UUID:-?}"
} | tee "$OUT/env.txt"

write_cfg() { # $1 datapath $2 worldname
  mkdir -p "$1/Saves"
  sed -e "s#__SAVE__#$1/Saves/bench.vcdbs#" -e "s#__WORLD__#$2#" -e "s#__MODDIR__#$B/mod#" "$B/serverconfig.bench.json" > "$1/serverconfig.json"
}

run_one() { # variant run mode
  local v=$1 r=$2 m=$3 dp="$DATA/$1-$2" out="$OUT/$1-run$2-$3.json"
  if [ "$m" = gen ]; then rm -rf "$dp"; write_cfg "$dp" "bench-$v-$r"; [ "$v" = stratum ] && cp "$B/stratum.json" "$dp/stratum.json"; fi
  local idle=$IDLE ents=$ENTITIES
  [ "$m" = load ] && { idle=10; ents=0; }
  echo "[$(date +%T)] $v run $r mode=$m -> $(basename "$out")"
  local t0=$SECONDS
  ( cd "$H/servers/$v" && env BENCH_LABEL="$v" BENCH_MODE="$m" BENCH_OUT="$out" BENCH_RADIUS="$RADIUS" BENCH_IDLE_SEC="$idle" \
      BENCH_SETTLE_SEC="$SETTLE" BENCH_ENTITIES="$ents" BENCH_ENTITY_SEC="$ENTITY_SEC" BENCH_SYSTIMING=1 BENCH_NONET=1 \
      BENCH_LADDER="$([ "$m" = gen ] && echo "$LADDER")" BENCH_STEP_SEC="$STEP_SEC" BENCH_SPAWN_PER_TICK="$SPAWN_PER_TICK" BENCH_LADDER_STOP_MS="$LADDER_STOP_MS" \
      DOTNET_TieredPGO=1 DOTNET_STARTUP_HOOKS="$B/nonet/NoNetHook.dll" \
      timeout --foreground "$TIMEOUT" dotnet VintagestoryServer.dll --dataPath "$dp" < /dev/null > "${out%.json}.stdout.log" 2>&1 )
  # --foreground + stdin from /dev/null: Wings attaches a TTY, and a background process group touching it gets SIGTTIN and stops (state "Tl").
  local rc=$?
  [ -f "$dp/Logs/server-main.log" ] && cp "$dp/Logs/server-main.log" "${out%.json}.server-main.log"
  local ok=MISSING; [ -f "$out" ] && ok=ok
  echo "   exit=$rc elapsed=$((SECONDS - t0))s result=$ok"
  echo "{\"variant\":\"$v\",\"run\":$r,\"mode\":\"$m\",\"rc\":$rc,\"elapsed_s\":$((SECONDS - t0)),\"ok\":\"$ok\"}" >> "$OUT/runs.jsonl"
}

IFS=, read -ra VS <<< "$VARIANTS"; IFS=, read -ra MS <<< "$MODES"
for ((r = 1; r <= RUNS; r++)); do
  if (( r % 2 == 1 )); then order=("${VS[@]}"); else order=(); for ((i = ${#VS[@]} - 1; i >= 0; i--)); do order+=("${VS[i]}"); done; fi
  for v in "${order[@]}"; do for m in "${MS[@]}"; do run_one "$v" "$r" "$m"; done; done
done
echo "BENCH DONE $(date -u +%FT%TZ)"; touch "$OUT/DONE"
# Hold the container open: Wings treats any exit (even 0) as a crash and restarts the server, which would
# rerun the benchmark and overwrite the results. Stop the server from the panel once the results are fetched.
echo "holding container open; stop the server from the panel"; sleep infinity
