export function PauseOverlay() {
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-stage/80 backdrop-blur-sm">
      <span className="font-display text-[12rem] uppercase leading-none tracking-widest">
        Пауза
      </span>
    </div>
  );
}
