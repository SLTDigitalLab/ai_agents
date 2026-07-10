/**
 * VoiceRecorder.jsx
 * WhatsApp-style voice recording component.
 *
 * Props:
 *   onSend(audioBlob, durationSeconds) — called when user hits send
 *   onCancel()                         — called when user cancels/deletes
 *   agentColor                         — agentConfig.color for gradient buttons
 */

import React, { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

const VoiceRecorder = ({ onSend, onCancel, agentColor }) => {
    //  State 
    const [phase, setPhase] = useState('recording'); // 'recording' | 'preview'
    const [elapsed, setElapsed] = useState(0);       // seconds recorded
    const [isPlaying, setIsPlaying] = useState(false);
    const [playProgress, setPlayProgress] = useState(0); // 0-1
    const [audioDuration, setAudioDuration] = useState(0);

    // Refs
    const mediaRecorderRef = useRef(null);
    const chunksRef = useRef([]);
    const timerRef = useRef(null);
    const audioBlobRef = useRef(null);
    const audioObjRef = useRef(null);
    const audioUrlRef = useRef(null);
    const streamRef = useRef(null);

    //  Start recording immediately on mount 
    useEffect(() => {
        startRecording();
        return () => {
            cleanup();
        };
    }, []);

    //  Timer tick every second while recording 
    useEffect(() => {
        if (phase === 'recording') {
            timerRef.current = setInterval(() => {
                setElapsed(prev => prev + 1);
            }, 1000);
        } else {
            clearInterval(timerRef.current);
        }
        return () => clearInterval(timerRef.current);
    }, [phase]);

    //  Helpers 
    const formatTime = (secs) => {
        const m = Math.floor(secs / 60).toString().padStart(2, '0');
        const s = (secs % 60).toString().padStart(2, '0');
        return `${m}:${s}`;
    };

    const cleanup = () => {
        clearInterval(timerRef.current);
        if (streamRef.current) {
            streamRef.current.getTracks().forEach(t => t.stop());
        }
        if (audioObjRef.current) {
            audioObjRef.current.pause();
            audioObjRef.current = null;
        }
        if (audioUrlRef.current) {
            URL.revokeObjectURL(audioUrlRef.current);
            audioUrlRef.current = null;
        }
    };

    //  Start mic recording 
    const startRecording = async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            streamRef.current = stream;

            const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus'
                : MediaRecorder.isTypeSupported('audio/webm')
                ? 'audio/webm'
                : 'audio/mp4';

            const recorder = new MediaRecorder(stream, { mimeType });
            mediaRecorderRef.current = recorder;
            chunksRef.current = [];

            recorder.ondataavailable = (e) => {
                if (e.data.size > 0) chunksRef.current.push(e.data);
            };

            recorder.onstop = () => {
                // Stop mic tracks
                stream.getTracks().forEach(t => t.stop());

                const blob = new Blob(chunksRef.current, { type: mimeType });
                audioBlobRef.current = blob;

                // Create object URL for local playback preview
                const url = URL.createObjectURL(blob);
                audioUrlRef.current = url;

                // Create audio element to get duration
                const audio = new Audio(url);
                audio.onloadedmetadata = () => {
                    const dur = isFinite(audio.duration) ? Math.round(audio.duration) : elapsed;
                    setAudioDuration(dur);
                };
                audioObjRef.current = audio;

                setPhase('preview');
            };

            recorder.start();
        } catch (err) {
            console.error('Mic error:', err);
            onCancel();
        }
    };

    //  Stop recording → go to preview 
    const handleStopRecording = () => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
            mediaRecorderRef.current.stop();
        }
    };

    //  Cancel / delete 
    const handleCancel = () => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
            mediaRecorderRef.current.stop();
        }
        cleanup();
        onCancel();
    };

    //  Play / pause preview 
    const handlePlayPause = () => {
        if (!audioObjRef.current) return;

        if (isPlaying) {
            audioObjRef.current.pause();
            setIsPlaying(false);
        } else {
            audioObjRef.current.play();
            setIsPlaying(true);

            // Track progress
            audioObjRef.current.ontimeupdate = () => {
                const audio = audioObjRef.current;
                if (audio && audio.duration) {
                    setPlayProgress(audio.currentTime / audio.duration);
                }
            };

            audioObjRef.current.onended = () => {
                setIsPlaying(false);
                setPlayProgress(0);
                audioObjRef.current.currentTime = 0;
            };
        }
    };

    //  Send to parent 
    const handleSend = () => {
        if (!audioBlobRef.current) return;
        cleanup();
        onSend(audioBlobRef.current, audioDuration || elapsed);
    };

    //  Waveform bars (decorative, 12 bars) 
    const WaveformBars = ({ active }) => (
        <div className="flex items-center gap-[3px] h-6">
            {Array.from({ length: 12 }).map((_, i) => {
                const heights = [3, 5, 8, 12, 9, 14, 10, 7, 11, 6, 9, 4];
                const h = heights[i];
                const filled = active && (i / 12) < playProgress;
                return (
                    <div
                        key={i}
                        className={`rounded-full transition-colors duration-100 ${
                            filled ? 'bg-blue-500' : 'bg-gray-300'
                        }`}
                        style={{ width: 3, height: h }}
                    />
                );
            })}
        </div>
    );

    //  Render 
    return (
        <AnimatePresence mode="wait">

            {/*  RECORDING phase  */}
            {phase === 'recording' && (
                <motion.div
                    key="recording"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={{ duration: 0.2 }}
                    className="relative flex items-center w-full bg-[#fbfcff]/95 backdrop-blur-3xl rounded-full border border-red-200/80 shadow-[0_12px_40px_-10px_rgba(0,0,0,0.1),inset_0_2px_4px_rgba(255,255,255,1)] p-0.5"
                >
                    {/* Cancel / delete button */}
                    <button
                        type="button"
                        onClick={handleCancel}
                        title="Cancel recording"
                        className="p-1.5 ml-1 rounded-full text-gray-400 hover:text-red-500 hover:bg-red-50 transition-all duration-200 shrink-0"
                    >
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                            <path fillRule="evenodd" d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z" clipRule="evenodd" />
                        </svg>
                    </button>

                    {/* Red pulsing dot */}
                    <div className="flex items-center gap-2 flex-1 px-3">
                        <span className="relative flex h-3 w-3 shrink-0">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                            <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500" />
                        </span>
                        <span className="text-red-500 text-sm font-medium tracking-wide">
                            Recording
                        </span>
                        {/* Animated recording bars */}
                        <div className="flex items-center gap-[2px] h-5 ml-1">
                            {[4,8,12,7,10,5,9,6].map((h, i) => (
                                <div
                                    key={i}
                                    className="w-[3px] rounded-full bg-red-400"
                                    style={{
                                        height: h,
                                        animation: `bounce 0.8s ease-in-out ${i * 0.1}s infinite alternate`,
                                    }}
                                />
                            ))}
                        </div>
                    </div>

                    {/* Timer */}
                    <span className="text-gray-500 text-sm font-mono tabular-nums mr-2 shrink-0">
                        {formatTime(elapsed)}
                    </span>

                    {/* Stop button — sends to preview */}
                    <button
                        type="button"
                        onClick={handleStopRecording}
                        title="Stop recording"
                        className={`relative p-1.5 rounded-full bg-gradient-to-tr ${agentColor} text-white shadow-md hover:shadow-lg hover:scale-105 transition-all duration-300 shrink-0`}
                    >
                        {/* Stop square icon */}
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                            <path d="M5.25 3A2.25 2.25 0 003 5.25v9.5A2.25 2.25 0 005.25 17h9.5A2.25 2.25 0 0017 14.75v-9.5A2.25 2.25 0 0014.75 3h-9.5z" />
                        </svg>
                    </button>
                </motion.div>
            )}

            {/*  PREVIEW phase  */}
            {phase === 'preview' && (
                <motion.div
                    key="preview"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={{ duration: 0.2 }}
                    className="relative flex items-center w-full bg-[#fbfcff]/95 backdrop-blur-3xl rounded-full border border-gray-200/80 shadow-[0_12px_40px_-10px_rgba(0,0,0,0.1),inset_0_2px_4px_rgba(255,255,255,1)] p-0.5"
                >
                    {/* Delete button */}
                    <button
                        type="button"
                        onClick={handleCancel}
                        title="Delete recording"
                        className="p-1.5 ml-1 rounded-full text-gray-400 hover:text-red-500 hover:bg-red-50 transition-all duration-200 shrink-0"
                    >
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                            <path fillRule="evenodd" d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z" clipRule="evenodd" />
                        </svg>
                    </button>

                    {/* Play / Pause button */}
                    <button
                        type="button"
                        onClick={handlePlayPause}
                        title={isPlaying ? "Pause" : "Play preview"}
                        className="p-1.5 ml-1 rounded-full text-gray-500 hover:text-blue-500 hover:bg-blue-50 transition-all duration-200 shrink-0"
                    >
                        {isPlaying ? (
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                                <path d="M5.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75A.75.75 0 007.25 3h-1.5zM12.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75a.75.75 0 00-.75-.75h-1.5z" />
                            </svg>
                        ) : (
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                                <path d="M6.3 2.841A1.5 1.5 0 004 4.11V15.89a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z" />
                            </svg>
                        )}
                    </button>

                    {/* Waveform + duration */}
                    <div className="flex items-center gap-2 flex-1 px-2">
                        <WaveformBars active={isPlaying} />
                        <span className="text-gray-400 text-xs font-mono tabular-nums shrink-0">
                            {formatTime(audioDuration || elapsed)}
                        </span>
                    </div>

                    {/* Send button */}
                    <button
                        type="button"
                        onClick={handleSend}
                        title="Send voice message"
                        className={`relative p-1.5 rounded-full bg-gradient-to-tr ${agentColor} text-white shadow-md hover:shadow-lg hover:scale-105 transition-all duration-300 shrink-0`}
                    >
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
                            <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                        </svg>
                    </button>
                </motion.div>
            )}
        </AnimatePresence>
    );
};

export default VoiceRecorder;