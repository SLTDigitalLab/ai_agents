/**
 * VoiceAgentPage.jsx
 * Live voice conversation — supports both OpenAI Realtime (WebRTC)
 * and Gemini Live (WebSocket proxy via backend).
 *
 * Sub-components (all in ./components/voice_agent/):
 *   VoiceOrb        — animated orb with rings & glow
 *   ListeningWave   — animated bar equaliser
 *   TranscriptPanel — scrollable transcript with header
 *   CallControls    — Start / Connecting / End Call button
 *   UserMenu        — avatar + logout dropdown
 *
 * Utilities:
 *   constants.js    — PHASE, API_URL, WS_URL, SYSTEM_PROMPT
 *   audioHelpers.js — float32ToPcm16Base64, pcm16Base64ToFloat32
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useMsal } from '@azure/msal-react';
import { motion as Motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

import sltLogo    from '../assets/slt-mobitel-logo.png';
import embryoLogo from '../assets/embryo-removebg.png';
import { useTheme } from '../contexts/ThemeContext';

import { PHASE, API_URL, WS_URL, SYSTEM_PROMPT } from '../components/voice_agent/constants';
import { float32ToPcm16Base64, pcm16Base64ToFloat32 } from '../components/voice_agent/AudioHelpers';
import VoiceOrb       from '../components/voice_agent/VoiceOrb';
import ListeningWave  from '../components/voice_agent/ListeningWave';
import TranscriptPanel from '../components/voice_agent/TranscriptPanel';
import CallControls   from '../components/voice_agent/CallControls';
import UserMenu       from '../components/voice_agent/UserMenu';

// Capture microphone audio off the main thread. Keeping the worklet prevents
// UI/rendering pauses from bursting queued audio at Gemini faster than real time.
const MIC_WORKLET_CODE = `
class MicProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) this.port.postMessage(input[0].slice());
    return true;
  }
}
registerProcessor('mic-processor', MicProcessor);
`;

// Approximately 256 ms at 16 kHz, matching Gemini's expected input cadence.
const MIC_CHUNK_SAMPLES = 4096;

//  Main component 
const VoiceAgentPage = () => {
    const { accounts, instance } = useMsal();
    const navigate  = useNavigate();
    const { theme, toggleTheme } = useTheme();
    const user      = accounts[0] || {};
    const firstName = (user.name || 'User').split(' ')[0];
    const initials  = (user.name || 'U').split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();

    //  State 
    const [phase,         setPhase]         = useState(PHASE.IDLE);
    const [isSpeaking,    setIsSpeaking]    = useState(false);
    const [isListening,   setIsListening]   = useState(false);
    const [transcript,    setTranscript]    = useState([]);
    const [statusText,    setStatusText]    = useState('Ready to connect');
    const [errorMessage,  setErrorMessage]  = useState('');
    const [showUserMenu,  setShowUserMenu]  = useState(false);

    // Refs
    const pcRef              = useRef(null);
    const dcRef              = useRef(null);
    const openaiAudioRef     = useRef(null);
    const geminiWsRef        = useRef(null);
    const audioContextRef    = useRef(null);
    const micWorkletRef      = useRef(null);
    const micStreamRef       = useRef(null);
    const nextPlayTimeRef    = useRef(0);
    const activeAudioSourcesRef = useRef([]);



    // Apply the global theme by toggling the `dark` class on <html>. This route
    // lives outside AgentWrapper, so it manages the class itself (Tailwind's
    // class strategy). Cleaned up on unmount so the destination page re-applies.
    useEffect(() => {
        const root = document.documentElement;
        if (theme === 'dark') root.classList.add('dark');
        else root.classList.remove('dark');
        return () => { root.classList.remove('dark'); };
    }, [theme]);

    //  Cleanup helpers 
    const cleanupOpenAI = useCallback(() => {
        if (dcRef.current)          { dcRef.current.close();          dcRef.current = null; }
        if (pcRef.current)          { pcRef.current.close();          pcRef.current = null; }
        if (openaiAudioRef.current)   openaiAudioRef.current.srcObject = null;
    }, []);

    const cleanupGemini = useCallback(() => {
        if (micWorkletRef.current)      { micWorkletRef.current.disconnect(); micWorkletRef.current = null; }
        if (micStreamRef.current)       { micStreamRef.current.getTracks().forEach(t => t.stop()); micStreamRef.current = null; }
        if (geminiWsRef.current)        { geminiWsRef.current.close();  geminiWsRef.current = null; }
        activeAudioSourcesRef.current.forEach(source => {
            try { source.stop(); } catch { /* already stopped */ }
        });
        activeAudioSourcesRef.current = [];
        if ('speechSynthesis' in window) window.speechSynthesis.cancel();
        if (audioContextRef.current)    { audioContextRef.current.close(); audioContextRef.current = null; }
        nextPlayTimeRef.current = 0;
    }, []);

    const cleanupAll = useCallback(() => { cleanupOpenAI(); cleanupGemini(); }, [cleanupOpenAI, cleanupGemini]);

    useEffect(() => () => cleanupAll(), [cleanupAll]);

    //  OpenAI helpers 
    const sendOpenAIEvent = useCallback((event) => {
        if (dcRef.current?.readyState === 'open') dcRef.current.send(JSON.stringify(event));
    }, []);

    const configureOpenAISession = useCallback(() => {
        sendOpenAIEvent({
            type: 'session.update',
            session: {
                type: 'realtime',
                instructions: SYSTEM_PROMPT,
                output_modalities: ['audio'],
                audio: {
                    input:  { turn_detection: { type: 'server_vad', threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 600 } },
                    output: { voice: 'ash' },
                },
                tool_choice: 'auto',
                tools: [
                        {
                            type: 'function',
                            name: 'ask_workmate_ai',
                            description: 'Send the user question to the Workmate AI agent pipeline. Use for ALL questions — HR, leave balance, finance, IT, admin, or any SLTMobitel workplace topic.',
                            parameters: {
                                type: 'object',
                                properties: {
                                    question: { type: 'string', description: "The user's question exactly as spoken" }
                                },
                                required: ['question'],
                            },
                        },
                    ],
            },
        });
    }, [sendOpenAIEvent]);

    const handleOpenAIMessage = useCallback(async (event) => {
        let msg; try { msg = JSON.parse(event.data); } catch { return; }

        switch (msg.type) {
            case 'session.created':
                configureOpenAISession();
                setStatusText('Speak to Workmate AI');
                setPhase(PHASE.CONNECTED);
                break;

            case 'response.audio.delta':       setIsSpeaking(true);  break;
            case 'response.audio.done':        setIsSpeaking(false); break;

            case 'input_audio_buffer.speech_started':
                setIsListening(true);  setStatusText('Listening...'); break;
            case 'input_audio_buffer.speech_stopped':
                setIsListening(false); setStatusText('Processing...'); break;

            case 'conversation.item.input_audio_transcription.completed':
                if (msg.transcript?.trim()) {
                    setTranscript(prev => [...prev, { role: 'user', text: msg.transcript.trim() }]);
                    setStatusText('Speak to Workmate AI');
                }
                break;

            case 'response.audio_transcript.delta':
                setTranscript(prev => {
                    const last = prev[prev.length - 1];
                    if (last?.role === 'assistant' && last?.partial)
                        return [...prev.slice(0, -1), { ...last, text: last.text + msg.delta }];
                    return [...prev, { role: 'assistant', text: msg.delta, partial: true }];
                });
                break;

            case 'response.audio_transcript.done':
                setTranscript(prev => {
                    const last = prev[prev.length - 1];
                    if (last?.role === 'assistant' && last?.partial)
                        return [...prev.slice(0, -1), { role: 'assistant', text: last.text }];
                    return prev;
                });
                setIsSpeaking(false);
                setStatusText('Speak to Workmate AI');
                break;

            case 'filler':
                    // play filler text via browser TTS so user hears it
                    // while the agent pipeline runs in the background
                    if ('speechSynthesis' in window) {
                        window.speechSynthesis.cancel(); // stop any previous
                        const utter = new SpeechSynthesisUtterance(msg.text);
                        // match voice language
                        const langMap = { en: 'en-US', si: 'si-LK', ta: 'ta-IN' };
                        utter.lang = langMap[msg.lang] || 'en-US';
                        utter.rate = 1.05;
                        utter.pitch = 1.0;
                        // try to find a voice that matches — fallback to default
                        const voices = window.speechSynthesis.getVoices();
                        const match = voices.find(v => v.lang.startsWith(utter.lang.split('-')[0]));
                        if (match) utter.voice = match;
                        window.speechSynthesis.speak(utter);
                    }
                    break;

            case 'response.function_call_arguments.done':
    if (msg.name === 'ask_workmate_ai') {
        try {
            const args = JSON.parse(msg.arguments);
            setStatusText('Checking knowledge base...');
            const res = await fetch(`${API_URL}/api/v1/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: args.question,
                    agent_id: 'supervisor',
                    user_id: user.username || 'anonymous',
                    user_name: user.name || '',
                    thread_id: `voice_openai_${Date.now()}`,
                    stream: false,
                }),
            });
            const data = res.ok ? await res.json() : {};
            const result = data.response || 'I could not find an answer to that.';
            sendOpenAIEvent({
                type: 'conversation.item.create',
                item: {
                    type: 'function_call_output',
                    call_id: msg.call_id,
                    output: result,
                },
            });
            sendOpenAIEvent({ type: 'response.create' });
            setStatusText('Speak to Workmate AI');
        } catch (err) {
            console.error('Voice agent call error:', err);
            sendOpenAIEvent({
                type: 'conversation.item.create',
                item: { type: 'function_call_output', call_id: msg.call_id, output: 'Unable to get answer right now.' },
            });
            sendOpenAIEvent({ type: 'response.create' });
        }
    }
    break;
            case 'error':
                setErrorMessage(msg.error?.message || 'An error occurred');
                setPhase(PHASE.ERROR);
                break;

            default: break;
        }
    }, [configureOpenAISession, sendOpenAIEvent, user.name, user.username]);

    //  Gemini audio playback 
    const playGeminiAudioChunk = useCallback((base64Data) => {
    if (!audioContextRef.current) return;
    const ctx     = audioContextRef.current;
    const float32 = pcm16Base64ToFloat32(base64Data);
    const buffer  = ctx.createBuffer(1, float32.length, 24000);
    buffer.copyToChannel(float32, 0);
    const source  = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    const startTime = Math.max(ctx.currentTime, nextPlayTimeRef.current);
    source.start(startTime);
    nextPlayTimeRef.current = startTime + buffer.duration;
    setIsSpeaking(true);

    // track this source so it can be stopped on interruption
    activeAudioSourcesRef.current.push(source);
    source.onended = () => {
        activeAudioSourcesRef.current = activeAudioSourcesRef.current.filter(s => s !== source);
        if (activeAudioSourcesRef.current.length === 0 &&
            nextPlayTimeRef.current <= ctx.currentTime) {
            setIsSpeaking(false);
        }
    };
}, []);


const stopAllAudio = useCallback(() => {
    // stop all queued and playing audio chunks immediately
    activeAudioSourcesRef.current.forEach(source => {
        try { source.stop(); } catch { /* already stopped */ }
    });
    activeAudioSourcesRef.current = [];
    nextPlayTimeRef.current = audioContextRef.current?.currentTime || 0;
    setIsSpeaking(false);
}, []);

    //  Start conversation 
    const startConversation = async () => {
        setPhase(PHASE.CONNECTING);
        setStatusText('Connecting...');
        setTranscript([]);
        setErrorMessage('');

        try {

            const providerRes   = await fetch(`${API_URL}/api/v1/realtime/provider`);
            const providerData  = await providerRes.json();
            const activeProvider = providerData.provider;
            if (activeProvider === 'gemini') await startGeminiSession();
            else                             await startOpenAISession();
        } catch (err) {
            setErrorMessage(err.message || 'Failed to start voice session');
            setPhase(PHASE.ERROR);
            cleanupAll();
        }
    };

    //  OpenAI WebRTC 
    const startOpenAISession = async () => {

        const tokenRes = await fetch(`${API_URL}/api/v1/realtime/token`);
        if (!tokenRes.ok) throw new Error('Failed to get OpenAI token');
        const { value: ephemeralKey } = await tokenRes.json();
        if (!ephemeralKey) throw new Error('No ephemeral token returned');

        setStatusText('Setting up audio...');
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        const pc     = new RTCPeerConnection();
        pcRef.current = pc;

        if (!openaiAudioRef.current) {
            openaiAudioRef.current = document.createElement('audio');
            openaiAudioRef.current.autoplay = true;
            document.body.appendChild(openaiAudioRef.current);
        }
        pc.ontrack = (e) => { openaiAudioRef.current.srcObject = e.streams[0]; };
        stream.getTracks().forEach(track => pc.addTrack(track, stream));

        const dc = pc.createDataChannel('oai-events');
        dcRef.current = dc;
        dc.onmessage = handleOpenAIMessage;
        dc.onerror   = () => { setErrorMessage('Connection error. Please try again.'); setPhase(PHASE.ERROR); };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        setStatusText('Negotiating connection...');

        const sdpRes = await fetch('https://api.openai.com/v1/realtime/calls?model=gpt-realtime', {
            method: 'POST',
            headers: { Authorization: `Bearer ${ephemeralKey}`, 'Content-Type': 'application/sdp' },
            body: offer.sdp,
        });
        if (!sdpRes.ok) throw new Error(`SDP failed: ${await sdpRes.text()}`);
        await pc.setRemoteDescription({ type: 'answer', sdp: await sdpRes.text() });
        setStatusText('Establishing voice channel...');
    };

    //  Gemini WebSocket 
    const startGeminiSession = async () => {
        setStatusText('Requesting microphone...');
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        micStreamRef.current = stream;

        const ctx = new AudioContext({ sampleRate: 16000 });
        audioContextRef.current  = ctx;
        nextPlayTimeRef.current  = ctx.currentTime;

        setStatusText('Connecting...');
        const ws = new WebSocket(`${WS_URL}/api/v1/realtime/ws/voice`);
        geminiWsRef.current = ws;

        await new Promise((resolve, reject) => {
            ws.onopen  = resolve;
            ws.onerror = () => reject(new Error('WebSocket failed'));
            setTimeout(() => reject(new Error('Timeout')), 10000);
        });


        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                switch (msg.type) {
                    case 'ready':
                        // send user identity so backend can personalise the greeting
                        ws.send(JSON.stringify({
                            type: 'user_identity',
                            user_id: user.username || '',
                            user_name: user.name || '',
                        }));
                        setStatusText('Speak to Workmate AI');
                        setPhase(PHASE.CONNECTED);
                        startMicCapture(ctx, ws);
                        break;
                    case 'audio':
                        window.speechSynthesis.cancel();
                        playGeminiAudioChunk(msg.data);
                        break;
                    case 'transcript':
                        if (msg.role === 'user') {
                            setIsListening(false);
                            setTranscript(prev => [...prev, { role: 'user', text: msg.text }]);
                        } else {
                            setTranscript(prev => {
                                const last = prev[prev.length - 1];
                                if (last?.role === 'assistant' && last?.partial)
                                    return [...prev.slice(0, -1), { ...last, text: last.text + msg.text }];
                                return [...prev, { role: 'assistant', text: msg.text, partial: true }];
                            });
                        }
                        setStatusText('Speak to Workmate AI');
                        break;
                    case 'turn_complete':
                        setTranscript(prev => {
                            const last = prev[prev.length - 1];
                            if (last?.role === 'assistant' && last?.partial)
                                return [...prev.slice(0, -1), { role: 'assistant', text: last.text }];
                            return prev;
                        });
                        break;
                    case 'listening':
                        setIsListening(true); setStatusText('Listening...'); break;
                    case 'session_end':
                        setStatusText('Session ended — click Start to reconnect');
                        setPhase(PHASE.IDLE);
                        cleanupGemini();
                        break;
                    case 'stop_audio':
                        stopAllAudio();
                        setStatusText('Listening...');
                        setIsListening(true);
                        break;
                    case 'error':
                        setErrorMessage(msg.message || 'Connection error');
                        setPhase(PHASE.ERROR);
                        cleanupGemini();
                        break;
                    default: break;
                }
            } catch { /* ignore parse errors */ }
        };

        ws.onclose = () => {
            if (phase === PHASE.CONNECTED) {
                setStatusText('Call ended — click Start to reconnect');
                setPhase(PHASE.IDLE);
            }
        };
        ws.onerror = () => { setErrorMessage('Connection to voice backend failed'); setPhase(PHASE.ERROR); };
    };

    const startMicCapture = async (ctx, ws) => {
        try {
            const blob = new Blob([MIC_WORKLET_CODE], { type: 'application/javascript' });
            const workletUrl = URL.createObjectURL(blob);
            await ctx.audioWorklet.addModule(workletUrl);
            URL.revokeObjectURL(workletUrl);

            const source = ctx.createMediaStreamSource(micStreamRef.current);
            const workletNode = new AudioWorkletNode(ctx, 'mic-processor');
            micWorkletRef.current = workletNode;

            let pending = [];
            let pendingLen = 0;

            workletNode.port.onmessage = (event) => {
                if (ws.readyState !== WebSocket.OPEN) return;
                pending.push(event.data);
                pendingLen += event.data.length;
                if (pendingLen < MIC_CHUNK_SAMPLES) return;

                const merged = new Float32Array(pendingLen);
                let offset = 0;
                for (const chunk of pending) {
                    merged.set(chunk, offset);
                    offset += chunk.length;
                }
                pending = [];
                pendingLen = 0;
                ws.send(JSON.stringify({ type: 'audio', data: float32ToPcm16Base64(merged) }));
            };

            source.connect(workletNode);
            // Keep the mic off ctx.destination to avoid local playback/feedback.
        } catch (err) {
            console.error('Failed to start AudioWorklet mic capture:', err);
            setErrorMessage('Microphone setup failed. Please refresh and try again.');
            setPhase(PHASE.ERROR);
        }
    };

    //  End call 
    const endConversation = useCallback(() => {
        if (geminiWsRef.current?.readyState === WebSocket.OPEN)
            geminiWsRef.current.send(JSON.stringify({ type: 'end' }));
        cleanupAll();
        setPhase(PHASE.IDLE);
        setIsSpeaking(false);
        setIsListening(false);
        setStatusText('Ready to connect');
    }, [cleanupAll]);

    const handleLogout = () => {
        endConversation();
        instance.logoutRedirect({ postLogoutRedirectUri: window.location.origin });
    };

    const isActive     = phase === PHASE.CONNECTED;
    const displayStatus = isListening ? 'Listening...' : isSpeaking ? 'Speaking...' : statusText;

    return (
        <div className="h-screen flex bg-[#fafafa] dark:bg-[#111318] text-gray-900 dark:text-gray-100 overflow-hidden">

            {/* ── Slim left sidebar (desktop only; mobile uses the top-bar cluster) ── */}
            <div className="hidden sm:flex w-14 flex-shrink-0 flex-col items-center py-4 gap-2 border-r border-gray-200 dark:border-white/[0.06] bg-white dark:bg-[#0d0f14]">
                <div className="flex-1" />

                {/* Theme toggle */}
                <button
                    onClick={toggleTheme}
                    title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                    className="w-9 h-9 flex items-center justify-center rounded-xl text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/[0.07] transition-all duration-200"
                >
                    {theme === 'dark' ? (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                            <path d="M10 2a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 2zM10 15a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 15zM10 7a3 3 0 100 6 3 3 0 000-6zM15.657 5.404a.75.75 0 10-1.06-1.06l-1.061 1.06a.75.75 0 001.06 1.06l1.06-1.06zM6.464 14.596a.75.75 0 10-1.06-1.06l-1.06 1.06a.75.75 0 001.06 1.06l1.06-1.06zM18 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 0118 10zM5 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 015 10zM14.596 15.657a.75.75 0 001.06-1.06l-1.06-1.061a.75.75 0 10-1.06 1.06l1.06 1.061zM5.404 6.464a.75.75 0 001.06-1.06l-1.06-1.06a.75.75 0 10-1.06 1.06l1.06 1.06z" />
                        </svg>
                    ) : (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                            <path fillRule="evenodd" d="M7.455 2.004a.75.75 0 01.26.77 7 7 0 009.958 7.967.75.75 0 011.067.853A8.5 8.5 0 116.647 1.921a.75.75 0 01.808.083z" clipRule="evenodd" />
                        </svg>
                    )}
                </button>

                <UserMenu
                    user={user}
                    initials={initials}
                    showMenu={showUserMenu}
                    onToggle={setShowUserMenu}
                    onLogout={handleLogout}
                />
            </div>

            {/*  Main area  */}
            <div className="flex-1 flex flex-col min-h-0 overflow-hidden">

                {/* Top bar */}
                <div className="relative flex items-center justify-between px-6 py-3 border-b border-gray-200 dark:border-white/[0.06] shrink-0">
                    {/* Back to Chat Agent — arrow slides left on hover */}
                    <Motion.button
                        type="button"
                        onClick={() => { endConversation(); navigate('/workmateai'); }}
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        title="Back to Chat Agent"
                        className="group flex items-center gap-2 pl-3 pr-4 sm:pl-4 sm:pr-5 py-2 rounded-full bg-gradient-to-r from-cyan-900 to-cyan-600 text-white text-sm font-semibold shadow-md hover:shadow-lg ring-1 ring-black/5 transition-all shrink-0"
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            fill="none"
                            viewBox="0 0 24 24"
                            strokeWidth={2.2}
                            stroke="currentColor"
                            className="w-4 h-4 transition-transform duration-300 ease-out group-hover:-translate-x-1"
                        >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
                        </svg>
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                            <path fillRule="evenodd" d="M4.848 2.771A49.144 49.144 0 0112 2.25c2.43 0 4.817.178 7.152.52 1.978.292 3.348 2.024 3.348 3.97v6.02c0 1.946-1.37 3.678-3.348 3.97a48.901 48.901 0 01-3.476.383.39.39 0 00-.297.17l-2.755 4.133a.75.75 0 01-1.248 0l-2.755-4.133a.39.39 0 00-.297-.17 48.9 48.9 0 01-3.476-.384c-1.978-.29-3.348-2.024-3.348-3.97V6.741c0-1.946 1.37-3.68 3.348-3.97z" clipRule="evenodd" />
                        </svg>
                        <span className="hidden sm:inline">Chat Agent</span>
                    </Motion.button>

                    <h1 className="absolute left-1/2 top-[calc(50%_+_3px)] -translate-x-1/2 -translate-y-1/2 text-lg sm:text-xl font-bold tracking-tight text-gray-950 dark:text-gray-100">Voice Agent</h1>

                    <img src={sltLogo} alt="SLTMobitel" className="h-7 sm:h-10 w-auto object-contain opacity-90 dark:opacity-80" />
                </div>

                {/*  Centre stage  */}
                <div className="flex-1 flex flex-col items-center justify-center px-6 pb-6 min-h-0 gap-0">

                    {/* Greeting — only on idle */}
                    <AnimatePresence>
                        {phase === PHASE.IDLE && (
                            <Motion.div
                                initial={{ opacity: 0, y: -8 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -8 }}
                                transition={{ duration: 0.3 }}
                                className="mb-6 text-center"
                            >
                                <p className="text-[0.75rem] uppercase tracking-[0.18em] font-semibold text-gray-400 dark:text-gray-600 mb-1">Welcome back</p>
                                <p className="text-xl font-semibold text-gray-800 dark:text-gray-200">{firstName}</p>
                            </Motion.div>
                        )}
                    </AnimatePresence>

                    {/* Orb */}
                    <VoiceOrb phase={phase} isSpeaking={isSpeaking} isListening={isListening} theme={theme} />

                    {/* Wave */}
                    <div className="mt-4 h-6 flex items-center justify-center">
                        {isActive ? (
                            <ListeningWave isListening={isListening} isSpeaking={isSpeaking} />
                        ) : (
                            <div className="h-5" />
                        )}
                    </div>

                    {/* Status text */}
                    <Motion.p
                        key={displayStatus}
                        initial={{ opacity: 0, y: 3 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.2 }}
                        className={`mt-2 text-[0.78rem] font-medium tracking-wide ${
                            phase === PHASE.ERROR   ? 'text-red-500 dark:text-red-400'
                            : isListening          ? 'text-teal-600 dark:text-teal-400'
                            : isSpeaking           ? 'text-cyan-600 dark:text-cyan-400'
                            : phase === PHASE.CONNECTING ? 'text-gray-500'
                            : phase === PHASE.CONNECTED  ? 'text-gray-500 dark:text-gray-400'
                            : 'text-gray-400 dark:text-gray-600'
                        }`}
                    >
                        {displayStatus}
                    </Motion.p>

                    {phase === PHASE.ERROR && errorMessage && (
                        <Motion.p
                            initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                            className="mt-2 text-xs text-red-500/70 dark:text-red-400/60 text-center max-w-xs leading-relaxed"
                        >
                            {errorMessage}
                        </Motion.p>
                    )}

                    {/* Call controls */}
                    <div className="mt-8">
                        <CallControls phase={phase} onStart={startConversation} onEnd={endConversation} />
                    </div>

                    {/* Transcript */}
                    <AnimatePresence>
                        {transcript.length > 0 && (
                            <TranscriptPanel
                                transcript={transcript}
                                onClear={() => setTranscript([])}
                            />
                        )}
                    </AnimatePresence>

                </div>

                {/* Attribution docked at the bottom, matching the chat interface */}
                <div className="w-full flex items-center justify-center gap-1.5 pb-3 pt-1 shrink-0 select-none cursor-default">
                    <span className="text-[0.65rem] uppercase tracking-wider font-semibold text-gray-500 dark:text-gray-400">WorkMate AI powered by VoiceGenie AI</span>
                    <div className="w-px h-3 bg-gray-300 dark:bg-gray-600" />
                    <img src={embryoLogo} alt="Embryo Logo" className="h-[20px] w-auto object-contain dark:brightness-110" />
                </div>
            </div>
        </div>
    );
};

export default VoiceAgentPage;
