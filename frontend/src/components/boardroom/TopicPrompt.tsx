import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, Search, Square, X } from "lucide-react";

/** Offered as one-tap starting points; the field is free text. */
const SUGGESTIONS = [
  "the 2026 copper supply squeeze",
  "cutting a datacentre's power budget",
  "splitting a startup's remaining runway",
  "allocating relief funding after a flood",
];

/**
 * Asks what the table will argue about, before the floor opens.
 *
 * The topic is handed identically to every agent as the shared premise, and is
 * what switches their `web_search` tool on — so this is also the moment the
 * session gets something real to be about.
 */
export function TopicPrompt({
  open,
  onCancel,
  onConfirm,
  onTranscribe,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: (topic: string | null) => void;
  onTranscribe: (audio: Blob) => Promise<string>;
}) {
  const [topic, setTopic] = useState("");
  const [phase, setPhase] = useState<"idle" | "recording" | "transcribing">("idle");
  const [micError, setMicError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const startRecording = useCallback(async () => {
    setMicError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (e) => chunksRef.current.push(e.data);
      recorder.start();
      recorderRef.current = recorder;
      setPhase("recording");
    } catch {
      setMicError("No microphone access — type the topic instead.");
    }
  }, []);

  const stopAndTranscribe = useCallback(async () => {
    const recorder = recorderRef.current;
    setPhase("transcribing");

    const blob = await new Promise<Blob>((resolve) => {
      if (recorder && recorder.state === "recording") {
        recorder.onstop = () => resolve(new Blob(chunksRef.current, { type: "audio/webm" }));
        recorder.stop();
      } else {
        resolve(new Blob([], { type: "audio/webm" }));
      }
    });
    stopTracks();

    try {
      const spoken = (await onTranscribe(blob)).trim();
      if (spoken) {
        // Fill the field rather than starting straight away: speech-to-text
        // mangles proper nouns often enough that a look before committing is
        // worth one extra click.
        setTopic(spoken);
        inputRef.current?.focus();
      } else {
        setMicError("Didn't catch that. Try again, or type it.");
      }
    } catch (e) {
      setMicError(e instanceof Error ? e.message : "Transcription failed.");
    } finally {
      setPhase("idle");
    }
  }, [onTranscribe, stopTracks]);

  // Never leave the mic open behind a closed dialog.
  useEffect(() => {
    if (!open) {
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      stopTracks();
      setPhase("idle");
      setMicError("");
    }
  }, [open, stopTracks]);

  useEffect(() => {
    if (open) {
      setTopic("");
      // Focus after paint, so the field is ready to type into immediately.
      const id = requestAnimationFrame(() => inputRef.current?.focus());
      return () => cancelAnimationFrame(id);
    }
  }, [open]);

  if (!open) return null;

  const trimmed = topic.trim();

  const submit = () => onConfirm(trimmed ? trimmed : null);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/85 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Choose what the table will discuss"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <div className="panel animate-slide-in w-full max-w-xl rounded-xl p-6">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="font-display text-xl font-bold tracking-tight text-glow text-primary">
              WHAT ARE THEY ARGUING ABOUT?
            </h2>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              Every party gets this same premise, and it is what they will argue over. Pick
              something people genuinely disagree about — the sharper the disagreement, the better
              the session.
            </p>
          </div>
          <button
            onClick={onCancel}
            aria-label="Cancel"
            className="rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="mt-5 flex items-center gap-2">
          <input
            ref={inputRef}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            maxLength={500}
            disabled={phase !== "idle"}
            placeholder={
              phase === "recording"
                ? "listening… say what they should argue about"
                : phase === "transcribing"
                  ? "transcribing…"
                  : "say it or type it — e.g. the 2026 copper supply squeeze"
            }
            className="min-w-0 flex-1 rounded-lg border border-border bg-secondary/40 px-4 py-3 font-mono text-sm text-foreground outline-none transition-colors focus:border-primary disabled:opacity-60"
          />
          <button
            onClick={phase === "recording" ? stopAndTranscribe : startRecording}
            disabled={phase === "transcribing"}
            aria-label={phase === "recording" ? "Stop and transcribe" : "Speak the topic"}
            className={`grid size-[46px] shrink-0 place-items-center rounded-lg border transition-colors ${
              phase === "recording"
                ? "animate-rec-pulse border-trust-neg text-trust-neg"
                : "border-border text-muted-foreground hover:border-agent-4 hover:text-agent-4"
            } disabled:opacity-50`}
          >
            {phase === "transcribing" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : phase === "recording" ? (
              <Square className="size-4" />
            ) : (
              <Mic className="size-4" />
            )}
          </button>
        </div>

        {micError && <p className="mt-2 font-mono text-[11px] text-trust-neg">{micError}</p>}

        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              onClick={() => setTopic(suggestion)}
              className="rounded-full border border-border px-3 py-1 font-mono text-[11px] text-muted-foreground transition-colors hover:border-primary hover:text-foreground"
            >
              {suggestion}
            </button>
          ))}
        </div>

        <p className="mt-4 flex items-start gap-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
          <Search className="mt-0.5 size-3.5 shrink-0 text-agent-4" />
          <span>
            With a topic set, agents may search the live web for a fact to argue with — which costs
            a second model call per turn, so rounds run slower. Skipping is faster, but leaves them
            with nothing in particular to argue about.
          </span>
        </p>

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={() => onConfirm(null)}
            className="rounded-lg border border-border px-4 py-2 font-mono text-xs tracking-widest text-muted-foreground transition-colors hover:text-foreground"
          >
            SKIP
          </button>
          <button
            onClick={submit}
            className="rounded-lg bg-primary px-5 py-2 font-mono text-xs font-bold tracking-widest text-background transition-opacity hover:opacity-90"
          >
            OPEN THE FLOOR
          </button>
        </div>
      </div>
    </div>
  );
}
