import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Square, Send, X } from "lucide-react";
import type { Agent, InjectOfferPayload, VoiceOfferResult } from "@/lib/negotiation/types";

type Phase = "recording" | "processing" | "preview" | "error";

export function VoiceModal({
  open,
  agents,
  onClose,
  onTranscribe,
  onConfirm,
}: {
  open: boolean;
  agents: Agent[];
  onClose: () => void;
  onTranscribe: (audio: Blob) => Promise<VoiceOfferResult>;
  onConfirm: (offer: InjectOfferPayload) => void;
}) {
  const [phase, setPhase] = useState<Phase>("recording");
  const [result, setResult] = useState<VoiceOfferResult | null>(null);
  const [error, setError] = useState<string>("");
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const rafRef = useRef(0);
  // Explicit <ArrayBuffer> — getByteTimeDomainData rejects the default
  // Uint8Array<ArrayBufferLike>, which could be backed by a SharedArrayBuffer.
  const levelsRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);

  const teardown = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    analyserRef.current = null;
  }, []);

  const drawWave = useCallback(() => {
    const canvas = canvasRef.current;
    const analyser = analyserRef.current;
    const data = levelsRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = rect.width;
    const h = rect.height;
    const css = getComputedStyle(document.documentElement);
    const color = css.getPropertyValue("--agent-4").trim() || "#fbbf24";

    const render = () => {
      ctx.clearRect(0, 0, w, h);
      const bars = 64;
      if (analyser && data) analyser.getByteTimeDomainData(data);
      ctx.fillStyle = color;
      ctx.shadowBlur = 14;
      ctx.shadowColor = color;
      for (let i = 0; i < bars; i++) {
        const v = data
          ? Math.abs(data[Math.floor((i / bars) * data.length)] - 128) / 128
          : Math.abs(Math.sin(i * 0.4 + Date.now() / 220)) * 0.35;
        const bh = Math.max(3, v * h * 0.85);
        ctx.fillRect((i / bars) * w + 2, (h - bh) / 2, w / bars - 4, bh);
      }
      rafRef.current = requestAnimationFrame(render);
    };
    render();
  }, []);

  useEffect(() => {
    if (!open) {
      teardown();
      return;
    }
    setPhase("recording");
    setResult(null);
    setError("");
    chunksRef.current = [];

    let cancelled = false;
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        const audioCtx = new AudioContext();
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        audioCtx.createMediaStreamSource(stream).connect(analyser);
        analyserRef.current = analyser;
        levelsRef.current = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
        const rec = new MediaRecorder(stream);
        rec.ondataavailable = (e) => chunksRef.current.push(e.data);
        rec.start();
        recorderRef.current = rec;
      } catch {
        // No mic permission — still show the waveform so the demo can proceed.
        levelsRef.current = null;
      }
      drawWave();
    })();

    return () => {
      cancelled = true;
      teardown();
    };
  }, [open, drawWave, teardown]);

  const stopAndSend = async () => {
    setPhase("processing");
    const rec = recorderRef.current;
    const blob = await new Promise<Blob>((resolve) => {
      if (rec && rec.state === "recording") {
        rec.onstop = () => resolve(new Blob(chunksRef.current, { type: "audio/webm" }));
        rec.stop();
      } else {
        resolve(new Blob([], { type: "audio/webm" }));
      }
    });
    teardown();
    try {
      const res = await onTranscribe(blob);
      setResult(res);
      setPhase("preview");
    } catch {
      setError("Transcription failed. Check the backend connection.");
      setPhase("error");
    }
  };

  if (!open) return null;
  const target = agents.find((a) => a.id === result?.offer?.to);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/85 p-4 backdrop-blur-sm">
      <div className="panel animate-slide-in w-full max-w-xl rounded-xl p-6">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="font-display text-xl font-bold tracking-tight text-glow text-agent-4">
              SAY SOMETHING
            </h2>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              {phase === "recording" && "Recording — say whatever you want to say."}
              {phase === "processing" && "Transcribing…"}
              {phase === "preview" &&
                "Said to the table. Everyone will answer it on their next turn."}
              {phase === "error" && error}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close voice input"
            className="rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>

        {phase !== "preview" && <canvas ref={canvasRef} className="mt-6 h-28 w-full" aria-hidden />}

        {phase === "preview" && result && (
          <div className="mt-6 space-y-4">
            <div className="rounded-lg border border-border bg-secondary/40 p-4 font-mono text-sm text-foreground">
              <span className="text-agent-4">“</span>
              {result.transcript}
              <span className="text-agent-4">”</span>
            </div>
            {result.offer && (
              <div className="flex flex-wrap items-center gap-3 font-mono text-sm">
                <span className="text-muted-foreground">PARSED:</span>
                <span className="text-agent-4 font-bold">OPERATOR</span>
                <span className="text-muted-foreground">→</span>
                <span className="font-bold" style={{ color: target?.color }}>
                  {target?.name ?? result.offer.to}
                </span>
                <span className="font-bold tabular-nums text-foreground">
                  {result.offer.amount} {result.offer.resource}
                </span>
                {result.confidence === "low" && (
                  <span className="rounded border border-trust-neg px-1.5 py-0.5 text-[10px] font-bold tracking-widest text-trust-neg">
                    LOW CONFIDENCE — CHECK BEFORE SENDING
                  </span>
                )}
              </div>
            )}
            {!result.offer && (
              <p className="font-mono text-xs text-trust-neg">
                Couldn't turn that into an offer. Close and try again, naming a party and an amount.
              </p>
            )}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-3">
          {phase === "recording" && (
            <button
              onClick={stopAndSend}
              className="animate-rec-pulse inline-flex items-center gap-2 rounded-md bg-destructive px-5 py-2.5 font-display text-sm font-bold text-destructive-foreground"
            >
              <Square className="size-4" /> STOP &amp; SEND
            </button>
          )}
          {phase === "processing" && (
            <span className="inline-flex items-center gap-2 font-mono text-sm text-muted-foreground">
              <Mic className="size-4 animate-pulse" /> processing…
            </span>
          )}
          {phase === "preview" && result?.offer && (
            <button
              onClick={() => {
                onConfirm(result.offer!);
                onClose();
              }}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2.5 font-display text-sm font-bold text-primary-foreground transition-transform hover:scale-105"
            >
              <Send className="size-4" /> INJECT OFFER
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
