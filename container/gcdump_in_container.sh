#!/bin/bash
# Heap dump pass inside the panel server: run each server once (fresh world, no creatures, long idle),
# take a dotnet-gcdump during idle, write the by-type report to /results/gcdump-<variant>.gcreport.txt.
set -u
H=/home/container; B=$H/bench; OUT=$H/results; DATA=$H/benchdata-gc
mkdir -p "$OUT" "$DATA"
export DOTNET_CLI_HOME=$H
# dotnet-gcdump is shipped in /home/container/tools/gcdump (framework-dependent, no package download needed)
export DOTNET_ROLL_FORWARD=Major   # the tool targets net8.0; the image only has the .NET 10 runtime
GCDUMP="dotnet $H/tools/gcdump/dotnet-gcdump.dll"
$GCDUMP --version || { echo "dotnet-gcdump not available"; sleep infinity; }

write_cfg() { mkdir -p "$1/Saves"; sed -e "s#__SAVE__#$1/Saves/bench.vcdbs#" -e "s#__WORLD__#$2#" -e "s#__MODDIR__#$B/mod#" "$B/serverconfig.bench.json" > "$1/serverconfig.json"; }
find_dotnet_pid() { for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = dotnet ] && grep -aq "VintagestoryServer.dll" $p/cmdline 2>/dev/null && { echo ${p#/proc/}; return; }; done; }

for v in stratum vanilla; do
  dp="$DATA/$v"; rm -rf "$dp"; write_cfg "$dp" "gcdump-$v"; [ "$v" = stratum ] && cp "$B/stratum.json" "$dp/stratum.json"
  echo "[$(date +%T)] $v: starting (gen, idle 240 s, no creatures)"
  ( cd "$H/servers/$v" && env BENCH_LABEL="$v" BENCH_MODE=gen BENCH_OUT="$OUT/gcdump-$v.json" BENCH_RADIUS=20 BENCH_IDLE_SEC=240 \
      BENCH_SETTLE_SEC=20 BENCH_ENTITIES=0 BENCH_ENTITY_SEC=1 BENCH_SYSTIMING=0 BENCH_NONET=1 DOTNET_TieredPGO=1 \
      DOTNET_STARTUP_HOOKS="$B/nonet/NoNetHook.dll" timeout --foreground 600 dotnet VintagestoryServer.dll --dataPath "$dp" < /dev/null > "$OUT/gcdump-$v.stdout.log" 2>&1 ) &
  sleep 120   # startup ~15 s + worldgen ~40 s + settle 20 s => well inside the idle phase
  PID=$(find_dotnet_pid); echo "[$(date +%T)] $v: dotnet pid=$PID"
  $GCDUMP collect -p "$PID" -o "$OUT/gcdump-$v.gcdump" 2>&1 | tail -2
  $GCDUMP report "$OUT/gcdump-$v.gcdump" > "$OUT/gcdump-$v.gcreport.txt" 2>&1
  echo "[$(date +%T)] $v: report lines $(wc -l < "$OUT/gcdump-$v.gcreport.txt"); $(head -3 "$OUT/gcdump-$v.gcreport.txt" | tr '\n' ' ')"
  rm -f "$OUT/gcdump-$v.gcdump"
  wait
done
echo "GCDUMP DONE $(date -u +%FT%TZ)"; touch "$OUT/GCDONE"
echo "holding container open; stop the server from the panel"; sleep infinity
