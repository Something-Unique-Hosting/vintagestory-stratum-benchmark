#!/bin/bash
# Build the tarball that goes into a game-server container's volume (/home/container) for container/run_in_container.sh.
# Usage: container/make_bundle.sh <vanilla server dir> <prepared stratum dir> <build dir with stratumbench/ and nonet/> <out.tgz>
set -e
VAN=${1:?vanilla server dir}; STR=${2:?stratum server dir}; BUILD=${3:?build dir}; OUT=${4:-bench-bundle.tgz}
HERE=$(cd "$(dirname "$0")" && pwd)
W=$(mktemp -d)
mkdir -p "$W/servers" "$W/bench/mod/stratumbench" "$W/bench/nonet"
rsync -a --exclude '.vanilla-cache' --exclude 'benchdata*' "$VAN/" "$W/servers/vanilla/"
rsync -a --exclude '.vanilla-cache' --exclude 'benchdata*' "$STR/" "$W/servers/stratum/"
cp "$BUILD/stratumbench/StratumBench.dll" "$BUILD/stratumbench/modinfo.json" "$W/bench/mod/stratumbench/"
cp "$BUILD/nonet/NoNetHook.dll" "$W/bench/nonet/"
cp "$HERE/run_in_container.sh" "$HERE/gcdump_in_container.sh" "$HERE/../harness/serverconfig.bench.json" "$HERE/../harness/stratum.json" "$W/bench/"
chmod +x "$W/bench/"*.sh
tar czf "$OUT" -C "$W" servers bench
rm -rf "$W"
ls -la "$OUT"
