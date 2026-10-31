using System;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;

// .NET startup hook (DOTNET_STARTUP_HOOKS). When BENCH_NONET=1 it stops the Vintage Story
// server from opening its TCP/UDP listen sockets. The benchmark is headless (no clients), so
// the server never needs to listen; this keeps the benchmark hermetic and lets it run in
// environments where listening sockets are blocked. Applied identically to vanilla and Stratum.
internal class StartupHook
{
    public static void Initialize()
    {
        if (Environment.GetEnvironmentVariable("BENCH_NONET") != "1") return;
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            var name = new AssemblyName(e.Name).Name;
            foreach (var dir in new[] { AppContext.BaseDirectory, Path.Combine(AppContext.BaseDirectory, "Lib") })
            {
                var p = Path.Combine(dir, name + ".dll");
                if (File.Exists(p)) return Assembly.LoadFrom(p);
            }
            return null;
        };
        try { Patch(); Console.WriteLine("[NoNetHook] server listen sockets disabled for benchmark"); }
        catch (Exception e) { Console.WriteLine("[NoNetHook] FAILED: " + e); }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    static void Patch()
    {
        var lib = Assembly.Load("VintagestoryLib");
        var tcp = lib.GetType("Vintagestory.Server.TcpNetServer", true);
        var udp = lib.GetType("Vintagestory.Server.Network.UdpNetServer", true);
        var h = new HarmonyLib.Harmony("suh.nonet");
        var skip = new HarmonyLib.HarmonyMethod(typeof(StartupHook).GetMethod(nameof(Skip), BindingFlags.Static | BindingFlags.Public));
        h.Patch(tcp.GetMethod("Start"), prefix: skip);
        h.Patch(udp.GetMethod("Start"), prefix: skip);
        h.Patch(udp.GetMethod("Dispose"), prefix: new HarmonyLib.HarmonyMethod(typeof(StartupHook).GetMethod(nameof(SkipDisposeIfNoSocket), BindingFlags.Static | BindingFlags.Public)));
    }

    public static bool Skip() => false;

    public static bool SkipDisposeIfNoSocket(object __instance)
    {
        var f = __instance.GetType().GetField("udpServer", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        return f == null || f.GetValue(__instance) != null;
    }
}
