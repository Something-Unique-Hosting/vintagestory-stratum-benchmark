using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using HarmonyLib;
using Vintagestory.API.Common;
using Vintagestory.API.Common.Entities;
using Vintagestory.API.MathTools;
using Vintagestory.API.Server;

namespace StratumBench
{
    // Headless, player-free server benchmark. Phases:
    //   1. worldgen  - request a square of chunk columns around spawn, time until all are loaded
    //   2. idle      - steady state with those chunks kept loaded
    //   3. entities  - spawn N always-active creatures, measure tick cost
    // Every main-thread tick is timed with a Harmony hook around ServerMain.Process/ProcessMain
    // (same technique as the Lithos Probe profiler). Results go to one JSON file, then the server shuts down.
    public class BenchModSystem : ModSystem
    {
        static readonly double TicksToMs = 1000.0 / Stopwatch.Frequency;

        string label, mode, outPath;
        int radius, idleSec, settleSec, entityCount, entitySec;
        // Load ladder: cumulative entity targets (e.g. "1000,2000,4000,8000"), each measured for stepSec after spawning finishes.
        int[] ladder = Array.Empty<int>(); int stepSec, spawnPerTick; double ladderStopMs;
        int ladderIdx = -1, spawnQueue; bool spawning;
        readonly List<Dictionary<string, object>> ladderResults = new List<Dictionary<string, object>>();
        internal static volatile int LadderStep = 0; // 0 outside the ladder, else 1-based step index (tick series tag = 40 + step)

        ICoreServerAPI sapi;
        Harmony harmony;
        Process proc;

        internal static volatile int Phase = 0; // 0 none, 1 worldgen, 2 settle, 3 idle, 4 entities, 5 done
        internal static readonly Stopwatch Clock = Stopwatch.StartNew();
        internal static readonly List<double[]> TickSeries = new List<double[]>(); // [t_ms, tick_ms, phase]
        internal static readonly Dictionary<int, List<double>> PhaseTicks = new Dictionary<int, List<double>>();
        [ThreadStatic] static long tickStart;
        [ThreadStatic] static bool armed;

        readonly List<Dictionary<string, object>> samples = new List<Dictionary<string, object>>();
        readonly Dictionary<string, object> result = new Dictionary<string, object>();
        int totalColumns, loadedColumns;
        double phaseStartMs, startupMs;
        TimeSpan phaseStartCpu;
        long phaseStartGcPause;
        int[] phaseStartGc = new int[3];
        long tickListenerId;
        int spawnedEntities;
        List<string> spawnedCodes = new List<string>();
        double phaseDeadlineMs;

        public override bool ShouldLoad(EnumAppSide side) => side == EnumAppSide.Server;
        public override double ExecuteOrder() => 0.05;

        public override void StartServerSide(ICoreServerAPI api)
        {
            sapi = api;
            proc = Process.GetCurrentProcess();
            label = Env("BENCH_LABEL", "unknown");
            mode = Env("BENCH_MODE", "gen");
            outPath = Env("BENCH_OUT", Path.Combine(api.DataBasePath, "bench-result.json"));
            radius = int.Parse(Env("BENCH_RADIUS", "16"));
            idleSec = int.Parse(Env("BENCH_IDLE_SEC", "20"));
            settleSec = int.Parse(Env("BENCH_SETTLE_SEC", "20"));
            entityCount = int.Parse(Env("BENCH_ENTITIES", "300"));
            entitySec = int.Parse(Env("BENCH_ENTITY_SEC", "45"));
            var lad = Env("BENCH_LADDER", "");
            if (lad.Length > 0) ladder = lad.Split(',').Select(s => int.Parse(s.Trim())).ToArray();
            stepSec = int.Parse(Env("BENCH_STEP_SEC", "30"));
            spawnPerTick = int.Parse(Env("BENCH_SPAWN_PER_TICK", "250"));
            ladderStopMs = double.Parse(Env("BENCH_LADDER_STOP_MS", "150"), System.Globalization.CultureInfo.InvariantCulture);

            for (int p = 1; p <= 4; p++) PhaseTicks[p] = new List<double>();

            harmony = new Harmony("suh.stratumbench");
            var serverMain = AccessTools.TypeByName("Vintagestory.Server.ServerMain");
            harmony.Patch(AccessTools.Method(serverMain, "Process"), prefix: new HarmonyMethod(AccessTools.Method(typeof(BenchModSystem), nameof(ProcessPrefix))));
            harmony.Patch(AccessTools.Method(serverMain, "ProcessMain"), postfix: new HarmonyMethod(AccessTools.Method(typeof(BenchModSystem), nameof(ProcessMainPostfix))));

            if (Env("BENCH_SYSTIMING", "0") == "1") { try { PatchSystemTimings(serverMain.Assembly); } catch (Exception e) { api.Logger.Warning("[StratumBench] system timing disabled: {0}", e.Message); } }
            api.Event.ServerRunPhase(EnumServerRunPhase.RunGame, OnRunGame);
            api.Logger.Notification("[StratumBench] armed: label={0} mode={1} radius={2} entities={3} ladder={4}", label, mode, radius, entityCount, lad);
        }

        // ---- optional per-ServerSystem timing (diagnostic) ----
        internal static readonly Dictionary<int, Dictionary<string, double[]>> SysTime = new Dictionary<int, Dictionary<string, double[]>>();
        [ThreadStatic] static long sysStart;
        void PatchSystemTimings(System.Reflection.Assembly lib)
        {
            for (int p = 1; p <= 4; p++) SysTime[p] = new Dictionary<string, double[]>();
            var baseType = lib.GetType("Vintagestory.Server.ServerSystem");
            var pre = new HarmonyMethod(AccessTools.Method(typeof(BenchModSystem), nameof(SysPrefix)));
            var post = new HarmonyMethod(AccessTools.Method(typeof(BenchModSystem), nameof(SysPostfix)));
            int n = 0;
            Type[] types;
            try { types = lib.GetTypes(); }
            catch (System.Reflection.ReflectionTypeLoadException e) { types = e.Types.Where(t => t != null).ToArray(); } // client-only types fail to load on Linux
            foreach (var t in types)
            {
                if (t.IsAbstract || !baseType.IsAssignableFrom(t) || t == baseType) continue;
                var m = t.GetMethod("OnServerTick", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.DeclaredOnly);
                if (m == null) continue;
                try { harmony.Patch(m, prefix: pre, postfix: post); n++; } catch (Exception e) { sapi.Logger.Warning("[StratumBench] could not patch {0}: {1}", t.Name, e.Message); }
            }
            var em = lib.GetType("Vintagestory.Server.ServerEventManager");
            foreach (var name in new[] { "TriggerGameTick", "TriggerGameTickDebug" })
            {
                try
                {
                    var m = em?.GetMethod(name, System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance);
                    if (m != null && m.DeclaringType != m.ReflectedType) m = m.DeclaringType.GetMethod(name, System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance); // patch where it is declared
                    if (m != null) { harmony.Patch(m, prefix: pre, postfix: post); n++; }
                }
                catch (Exception e) { sapi.Logger.Warning("[StratumBench] could not patch {0}: {1}", name, e.Message); }
            }
            try
            {
                var pm = lib.GetType("Vintagestory.Server.ServerMain").GetMethod("ProcessMainThreadTasks");
                if (pm != null) { harmony.Patch(pm, prefix: pre, postfix: post); n++; }
            }
            catch (Exception e) { sapi.Logger.Warning("[StratumBench] could not patch ProcessMainThreadTasks: {0}", e.Message); }
            sapi.Logger.Notification("[StratumBench] system timing enabled on {0} methods", n);
        }
        public static void SysPrefix() { sysStart = Stopwatch.GetTimestamp(); }
        public static void SysPostfix(System.Reflection.MethodBase __originalMethod)
        {
            int p = Phase; if (p < 1 || p > 4) return;
            double ms = (Stopwatch.GetTimestamp() - sysStart) * TicksToMs;
            string key = __originalMethod.DeclaringType.Name + "." + __originalMethod.Name;
            var d = SysTime[p];
            if (!d.TryGetValue(key, out var acc)) d[key] = acc = new double[3];
            acc[0] += ms; acc[1] += 1; if (ms > acc[2]) acc[2] = ms;
        }

        static string Env(string k, string d) { var v = Environment.GetEnvironmentVariable(k); return string.IsNullOrEmpty(v) ? d : v; }

        public static void ProcessPrefix() { tickStart = Stopwatch.GetTimestamp(); armed = true; }
        public static void ProcessMainPostfix()
        {
            if (!armed) return;
            armed = false;
            int p = Phase;
            if (p < 1 || p > 4) return;
            double ms = (Stopwatch.GetTimestamp() - tickStart) * TicksToMs;
            PhaseTicks[p].Add(ms);
            int step = LadderStep;
            TickSeries.Add(new[] { Clock.Elapsed.TotalMilliseconds, ms, p == 4 && step > 0 ? 40 + step : p });
        }

        void OnRunGame()
        {
            startupMs = (DateTime.Now - proc.StartTime).TotalMilliseconds;
            result["label"] = label; result["mode"] = mode; result["radius"] = radius;
            result["entity_count_requested"] = entityCount;
            result["startup_ms"] = startupMs;
            result["seed"] = sapi.World.Seed;
            result["cpu"] = Environment.ProcessorCount;
            result["runtime"] = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription;
            result["gc_server"] = System.Runtime.GCSettings.IsServerGC;
            sapi.Logger.Notification("[StratumBench] RunGame reached after {0:0} ms, starting worldgen phase", startupMs);

            tickListenerId = sapi.Event.RegisterGameTickListener(OnTick, 250);
            BeginPhase(1);

            var spawn = sapi.World.DefaultSpawnPosition;
            int cx = (int)spawn.X / 32, cz = (int)spawn.Z / 32;
            result["spawn_chunk"] = new[] { cx, cz };
            var cols = new List<int[]>();
            for (int dz = -radius; dz <= radius; dz++)
                for (int dx = -radius; dx <= radius; dx++)
                    cols.Add(new[] { cx + dx, cz + dz });
            cols.Sort((a, b) => (Math.Abs(a[0] - cx) + Math.Abs(a[1] - cz)).CompareTo(Math.Abs(b[0] - cx) + Math.Abs(b[1] - cz)));
            totalColumns = cols.Count;
            foreach (var c in cols)
            {
                sapi.WorldManager.LoadChunkColumnPriority(c[0], c[1], new ChunkLoadOptions
                {
                    KeepLoaded = true,
                    OnLoaded = () => System.Threading.Interlocked.Increment(ref loadedColumns)
                });
            }
            sapi.Logger.Notification("[StratumBench] requested {0} chunk columns (radius {1}) around chunk {2},{3}", totalColumns, radius, cx, cz);
        }

        void BeginPhase(int p)
        {
            PhaseTicks[p] = new List<double>();            // a phase id can be reused by ladder steps
            if (SysTime.ContainsKey(p)) SysTime[p] = new Dictionary<string, double[]>();
            Phase = p;
            phaseStartMs = Clock.Elapsed.TotalMilliseconds;
            phaseStartCpu = proc.TotalProcessorTime;
            phaseStartGcPause = GC.GetTotalPauseDuration().Ticks;
            for (int g = 0; g < 3; g++) phaseStartGc[g] = GC.CollectionCount(g);
        }

        Dictionary<string, object> EndPhase(string name, Dictionary<string, object> extra = null)
        {
            int p = Phase;
            Phase = 0;
            proc.Refresh();
            var ticks = PhaseTicks[p];
            var d = new Dictionary<string, object>
            {
                ["wall_ms"] = Clock.Elapsed.TotalMilliseconds - phaseStartMs,
                ["cpu_ms"] = (proc.TotalProcessorTime - phaseStartCpu).TotalMilliseconds,
                ["gc_pause_ms"] = (GC.GetTotalPauseDuration().Ticks - phaseStartGcPause) / 10000.0,
                ["gc_gen0"] = GC.CollectionCount(0) - phaseStartGc[0],
                ["gc_gen1"] = GC.CollectionCount(1) - phaseStartGc[1],
                ["gc_gen2"] = GC.CollectionCount(2) - phaseStartGc[2],
                ["tick_count"] = ticks.Count,
                ["tick_mean_ms"] = ticks.Count > 0 ? ticks.Average() : 0,
                ["tick_p50_ms"] = Pct(ticks, 0.50),
                ["tick_p95_ms"] = Pct(ticks, 0.95),
                ["tick_p99_ms"] = Pct(ticks, 0.99),
                ["tick_max_ms"] = ticks.Count > 0 ? ticks.Max() : 0,
                ["ticks"] = ticks.ToArray(),
                ["working_set_end_mb"] = proc.WorkingSet64 / 1048576.0,
                ["managed_heap_end_mb"] = GC.GetTotalMemory(false) / 1048576.0,
            };
            if (SysTime.TryGetValue(p, out var st)) d["systems_ms"] = st.OrderByDescending(kv => kv.Value[0]).ToDictionary(kv => kv.Key, kv => new { total_ms = kv.Value[0], calls = kv.Value[1], max_ms = kv.Value[2] });
            if (extra != null) foreach (var kv in extra) d[kv.Key] = kv.Value;
            if (name != null) result[name] = d;
            sapi.Logger.Notification("[StratumBench] phase {0} done: wall {1:0} ms, cpu {2:0} ms, ticks {3}, mean {4:0.00} ms, p99 {5:0.00} ms",
                name ?? "ladder", d["wall_ms"], d["cpu_ms"], ticks.Count, d["tick_mean_ms"], d["tick_p99_ms"]);
            return d;
        }

        static double Pct(List<double> v, double p)
        {
            if (v.Count == 0) return 0;
            var s = v.OrderBy(x => x).ToArray();
            double idx = p * (s.Length - 1); int lo = (int)Math.Floor(idx); int hi = Math.Min(lo + 1, s.Length - 1);
            return s[lo] + (s[hi] - s[lo]) * (idx - lo);
        }

        void OnTick(float dt)
        {
            double now = Clock.Elapsed.TotalMilliseconds;
            proc.Refresh();
            samples.Add(new Dictionary<string, object>
            {
                ["t_ms"] = now, ["phase"] = Phase,
                ["loaded_columns"] = loadedColumns,
                ["working_set_mb"] = proc.WorkingSet64 / 1048576.0,
                ["managed_heap_mb"] = GC.GetTotalMemory(false) / 1048576.0,
                ["threads"] = proc.Threads.Count,
                ["entities"] = sapi.World.LoadedEntities.Count,
                ["cpu_ms"] = proc.TotalProcessorTime.TotalMilliseconds,
            });

            switch (Phase)
            {
                case 1:
                    if (loadedColumns >= totalColumns)
                    {
                        EndPhase("worldgen", new Dictionary<string, object> { ["columns"] = totalColumns, ["chunks"] = totalColumns * (sapi.WorldManager.MapSizeY / 32) });
                        BeginPhase(2); // settle: let background chunk work (neighbours, lighting, saving) finish
                        phaseDeadlineMs = now + settleSec * 1000.0;
                    }
                    else if (now - phaseStartMs > 20 * 60 * 1000)
                    {
                        sapi.Logger.Error("[StratumBench] worldgen timed out with {0}/{1} columns", loadedColumns, totalColumns);
                        result["error"] = "worldgen timeout"; Finish();
                    }
                    break;
                case 2:
                    if (now >= phaseDeadlineMs)
                    {
                        EndPhase("settle", new Dictionary<string, object> { ["entities_present"] = sapi.World.LoadedEntities.Count });
                        BeginPhase(3);
                        phaseDeadlineMs = now + idleSec * 1000.0;
                    }
                    break;
                case 3:
                    if (now >= phaseDeadlineMs)
                    {
                        EndPhase("idle", new Dictionary<string, object> { ["entities_present"] = sapi.World.LoadedEntities.Count });
                        if (ladder.Length > 0) { result["ladder_targets"] = ladder; result["ladder_step_sec"] = stepSec; NextLadderStep(); }
                        else if (entityCount > 0) { SpawnEntities(entityCount); BeginPhase(4); phaseDeadlineMs = now + entitySec * 1000.0; }
                        else Finish();
                    }
                    break;
                case 0:
                    if (spawning)
                    {
                        // spawn in slices so the spawn itself is not one giant tick; nothing is measured until the queue drains
                        int n = Math.Min(spawnPerTick, spawnQueue);
                        if (n > 0) { SpawnEntities(n); spawnQueue -= n; }
                        if (spawnQueue <= 0)
                        {
                            spawning = false;
                            sapi.Logger.Notification("[StratumBench] ladder step {0}: target {1}, loaded entities {2}", ladderIdx + 1, ladder[ladderIdx], sapi.World.LoadedEntities.Count);
                            LadderStep = ladderIdx + 1; BeginPhase(4); phaseDeadlineMs = now + stepSec * 1000.0;
                        }
                    }
                    break;
                case 4:
                    if (now >= phaseDeadlineMs && LadderStep > 0)
                    {
                        int activeL = sapi.World.LoadedEntities.Values.Count(e => e.State == EnumEntityState.Active);
                        var d = EndPhase(null, new Dictionary<string, object>
                        {
                            ["step"] = LadderStep, ["entities_target"] = ladder[ladderIdx], ["entities_spawned_total"] = spawnedEntities,
                            ["entities_end"] = sapi.World.LoadedEntities.Count, ["entities_active_end"] = activeL,
                        });
                        d.Remove("ticks"); // the tick series already holds every tick, tagged 40+step
                        ladderResults.Add(d); result["ladder"] = ladderResults;
                        LadderStep = 0;
                        double mean = (double)d["tick_mean_ms"];
                        if (ladderIdx + 1 >= ladder.Length || mean > ladderStopMs)
                        {
                            if (mean > ladderStopMs) sapi.Logger.Notification("[StratumBench] ladder stopped early: mean tick {0:0} ms exceeds {1:0} ms", mean, ladderStopMs);
                            Finish();
                        }
                        else NextLadderStep();
                    }
                    else if (now >= phaseDeadlineMs)
                    {
                        int active = sapi.World.LoadedEntities.Values.Count(e => e.State == EnumEntityState.Active);
                        EndPhase("entities", new Dictionary<string, object>
                        {
                            ["entities_spawned"] = spawnedEntities,
                            ["entities_end"] = sapi.World.LoadedEntities.Count,
                            ["entities_active_end"] = active,
                            ["codes"] = spawnedCodes.Distinct().ToArray(),
                        });
                        Finish();
                    }
                    break;
            }
        }

        void NextLadderStep()
        {
            ladderIdx++;
            spawnQueue = Math.Max(0, ladder[ladderIdx] - spawnedEntities);
            spawning = true; Phase = 0; LadderStep = 0;
            sapi.Logger.Notification("[StratumBench] ladder step {0}: spawning {1} more entities toward {2}", ladderIdx + 1, spawnQueue, ladder[ladderIdx]);
        }

        Random spawnRnd = new Random(1234);
        int spawnSerial;
        void SpawnEntities(int count)
        {
            string[] codes = { "wolf-eurasian-adult-male", "drifter-normal", "deer-whitetail-adult-female", "fox-red-adult-male", "hare-european-adult-female", "drifter-deep" };
            var rnd = spawnRnd;
            var spawn = sapi.World.DefaultSpawnPosition;
            int range = Math.Max(32, (radius - 2) * 32);
            int ok = 0, fail = 0;
            for (int i = 0; i < count; i++)
            {
                string code = codes[spawnSerial++ % codes.Length];
                var type = sapi.World.GetEntityType(new AssetLocation("game", code));
                if (type == null) { fail++; if (fail < 5) sapi.Logger.Warning("[StratumBench] unknown entity {0}", code); continue; }
                double x = spawn.X + rnd.Next(-range, range + 1) + 0.5;
                double z = spawn.Z + rnd.Next(-range, range + 1) + 0.5;
                int y = sapi.World.BlockAccessor.GetRainMapHeightAt((int)x, (int)z);
                if (y <= 0) { fail++; continue; }
                var entity = sapi.ClassRegistry.CreateEntity(type);
                entity.ServerPos.SetPos(x, y + 1.5, z);
                entity.ServerPos.Yaw = (float)(rnd.NextDouble() * Math.PI * 2);
                entity.Pos.SetFrom(entity.ServerPos);
                entity.AlwaysActive = true; // keep AI + physics running with no players online
                sapi.World.SpawnEntity(entity);
                entity.State = EnumEntityState.Active;
                entity.SidedProperties?.Behaviors?.RemoveAll(b => b.PropertyName() == "timeddespawn");
                ok++; spawnedCodes.Add(code);
            }
            spawnedEntities += ok;
            if (ladder.Length == 0 || fail > 0) sapi.Logger.Notification("[StratumBench] spawned {0} entities ({1} failed), loaded entities now {2}", ok, fail, sapi.World.LoadedEntities.Count);
        }

        void Finish()
        {
            Phase = 5;
            sapi.Event.UnregisterGameTickListener(tickListenerId);
            proc.Refresh();
            result["total_cpu_ms"] = proc.TotalProcessorTime.TotalMilliseconds;
            result["peak_working_set_mb"] = proc.PeakWorkingSet64 / 1048576.0;
            result["samples"] = samples;
            result["tick_series"] = TickSeries;
            result["finished_utc"] = DateTime.UtcNow.ToString("o");
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(outPath));
                File.WriteAllText(outPath, JsonSerializer.Serialize(result));
                sapi.Logger.Notification("[StratumBench] wrote {0}", outPath);
            }
            catch (Exception e) { sapi.Logger.Error("[StratumBench] failed to write result: {0}", e); }
            sapi.Logger.Notification("[StratumBench] shutting down");
            sapi.Server.ShutDown();
        }

        public override void Dispose() { harmony?.UnpatchAll("suh.stratumbench"); }
    }
}
