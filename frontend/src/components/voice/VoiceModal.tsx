import { useState, useEffect, useRef } from 'react';
import { useEngine } from '../../contexts/EngineContext';
import { motion, AnimatePresence } from 'framer-motion';
import { Mic, Square, Send, X } from 'lucide-react';

export function VoiceModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { state, injectHumanOffer } = useEngine();
  const [isRecording, setIsRecording] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [audioLevel, setAudioLevel] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationFrameRef = useRef<number>();

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      mediaRecorderRef.current = new MediaRecorder(stream);
      
      // Audio visualization setup
      audioContextRef.current = new AudioContext();
      analyserRef.current = audioContextRef.current.createAnalyser();
      const source = audioContextRef.current.createMediaStreamSource(stream);
      source.connect(analyserRef.current);
      analyserRef.current.fftSize = 256;
      const bufferLength = analyserRef.current.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      const updateLevel = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(dataArray);
        const average = dataArray.reduce((acc, val) => acc + val, 0) / bufferLength;
        setAudioLevel(average);
        animationFrameRef.current = requestAnimationFrame(updateLevel);
      };
      updateLevel();

      mediaRecorderRef.current.start();
      setIsRecording(true);
      setTranscript('Listening...');

      // Mock transcription progress
      setTimeout(() => {
        if (mediaRecorderRef.current?.state === 'recording') {
          setTranscript('Transcribing... "I offer ARIA 250 units."');
        }
      }, 3000);

    } catch (err) {
      console.error("Mic access denied", err);
      // Fallback for demo without mic access
      setIsRecording(true);
      setTranscript('Transcribing... "I offer ARIA 250 units." (Simulated)');
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    if (audioContextRef.current) audioContextRef.current.close();
    
    setIsRecording(false);
    setAudioLevel(0);
    setTranscript('Parsed Offer: Transfer 250 units to ARIA.');
  };

  const submitOffer = () => {
    // Hardcoded parsed offer for the demo
    injectHumanOffer(250, 'aria');
    onClose();
  };

  // Reset state on close
  useEffect(() => {
    if (!isOpen) {
      setIsRecording(false);
      setTranscript('');
      setAudioLevel(0);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <motion.div 
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="absolute inset-0 bg-background/80 backdrop-blur-sm"
          onClick={onClose}
        />
        
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 20 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 20 }}
          className="relative w-full max-w-lg bg-card border border-border rounded-xl shadow-[0_0_50px_rgba(0,0,0,0.5)] overflow-hidden"
        >
          <div className="p-4 border-b border-border flex items-center justify-between bg-background/50">
            <h3 className="font-bold uppercase tracking-widest text-primary flex items-center gap-2">
              <Mic className="w-4 h-4" /> Voice Injection Protocol
            </h3>
            <button onClick={onClose} className="text-muted-foreground hover:text-white transition-colors cursor-pointer">
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="p-8 flex flex-col items-center gap-8">
            {/* Visualizer */}
            <div className="relative w-32 h-32 flex items-center justify-center">
              <div 
                className="absolute inset-0 rounded-full bg-primary/20 transition-all duration-75"
                style={{ 
                  transform: `scale(${1 + (audioLevel / 255) * 1.5})`,
                  opacity: isRecording ? 1 : 0
                }}
              />
              <button
                onClick={isRecording ? stopRecording : startRecording}
                className={`relative z-10 w-20 h-20 rounded-full flex items-center justify-center transition-colors cursor-pointer ${
                  isRecording 
                    ? 'bg-destructive text-white shadow-[0_0_20px_rgba(239,68,68,0.5)] animate-pulse-ring' 
                    : 'bg-primary text-primary-foreground hover:bg-primary/90 shadow-[0_0_20px_rgba(0,212,255,0.3)]'
                }`}
              >
                {isRecording ? <Square className="w-8 h-8 fill-current" /> : <Mic className="w-8 h-8" />}
              </button>
            </div>

            {/* Transcript Area */}
            <div className="w-full h-24 bg-background border border-border rounded p-4 font-mono text-sm text-center flex flex-col justify-center gap-2">
              {!isRecording && !transcript && (
                <span className="text-muted-foreground">Press mic to dictate offer...</span>
              )}
              {transcript && (
                <span className={isRecording ? 'text-white animate-pulse' : 'text-primary'}>
                  {transcript}
                </span>
              )}
            </div>

            {/* Actions */}
            <div className="w-full flex justify-end gap-3">
              <button
                onClick={onClose}
                className="px-4 py-2 font-bold uppercase tracking-wider text-sm text-muted-foreground hover:text-white transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                onClick={submitOffer}
                disabled={isRecording || !transcript}
                className="px-6 py-2 bg-primary text-primary-foreground font-bold uppercase tracking-wider text-sm rounded flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-primary/90 transition-colors shadow-[var(--shadow-neon-blue)] cursor-pointer"
              >
                <Send className="w-4 h-4" /> Submit Offer
              </button>
            </div>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
