export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4 relative overflow-hidden">
      {/* CRT scanline texture */}
      <div className="scanline-overlay" />

      {/* Fine grid — amber-tinted for Bloomberg look */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            "linear-gradient(#f59e0b 1px, transparent 1px), linear-gradient(90deg, #f59e0b 1px, transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />

      {/* Primary amber glow */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 h-80 w-[560px] rounded-full bg-amber-500/6 blur-3xl pointer-events-none" />
      {/* Secondary accent */}
      <div className="absolute bottom-1/4 right-1/4 h-48 w-48 rounded-full bg-amber-600/4 blur-3xl pointer-events-none" />

      <div className="relative z-10 w-full max-w-sm fade-up">{children}</div>
    </div>
  );
}
