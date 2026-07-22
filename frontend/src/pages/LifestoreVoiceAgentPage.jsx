import React, { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

import sltLogo from '../assets/slt-mobitel-logo.png';
import embryoLogo from '../assets/embryo-removebg.png';
import { useTheme } from '../contexts/ThemeContext';

import LifestoreCheckout from '../components/forms/LifestoreCheckout';
import { API_URL, PHASE, WS_URL } from '../components/voice_agent/constants';
import { float32ToPcm16Base64, pcm16Base64ToFloat32 } from '../components/voice_agent/AudioHelpers';
import VoiceOrb from '../components/voice_agent/VoiceOrb';
import ListeningWave from '../components/voice_agent/ListeningWave';
import TranscriptPanel from '../components/voice_agent/TranscriptPanel';
import CallControls from '../components/voice_agent/CallControls';

const LIFESTORE_REALTIME_PATH = '/api/v1/realtime/lifestore';
const ASK_LIFESTORE_TOOL = 'ask_lifestore_chat';

const LIFESTORE_SYSTEM_PROMPT = `You are Ask LifeStore, a live voice assistant for SLTMobitel LifeStore customers.
For every LifeStore product, category, availability, comparison, cart, or checkout request, call ask_lifestore_chat.
Do not answer product facts, prices, stock status, seller names, cart totals, or checkout details from memory.
Keep spoken replies short and natural. Do not use markdown tables in voice replies.
Never speak raw JSON, hidden product-card metadata, checkout tokens, URLs, or implementation markers.
When checkout is prepared, tell the customer the checkout is ready on screen and that this is a sandbox demo if the chat result says so.`;

const createVoiceThreadId = () => {
    const random =
        typeof crypto !== 'undefined' && crypto.randomUUID
            ? crypto.randomUUID()
            : Math.random().toString(36).slice(2);
    return `lifestore-voice-${Date.now()}-${random}`;
};

const ProductCardsPanel = ({ cards, onClear }) => {
    if (!cards.length) return null;

    return (
        <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="w-full max-w-4xl mt-5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-white/[0.08] rounded-2xl shadow-lg overflow-hidden"
        >
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-white/[0.06]">
                <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-100">LifeStore products</h2>
                <button
                    type="button"
                    onClick={onClear}
                    className="text-xs font-medium text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
                >
                    Clear
                </button>
            </div>
            <div className="flex gap-3 overflow-x-auto p-4">
                {cards.slice(0, 12).map((card, index) => (
                    <a
                        key={card.id || card.product_id || card.url || `${card.name}-${index}`}
                        href={card.url || undefined}
                        target={card.url ? '_blank' : undefined}
                        rel={card.url ? 'noopener noreferrer' : undefined}
                        className="w-52 shrink-0 rounded-xl border border-gray-200 dark:border-white/[0.08] bg-gray-50 dark:bg-gray-950 overflow-hidden hover:shadow-md transition-shadow"
                    >
                        <div className="aspect-[4/3] bg-white dark:bg-gray-900 flex items-center justify-center">
                            {card.image_url ? (
                                <img
                                    src={card.image_url}
                                    alt={card.name || 'LifeStore product'}
                                    className="h-full w-full object-contain p-2"
                                />
                            ) : (
                                <span className="text-xs text-gray-400 px-3 text-center">No image</span>
                            )}
                        </div>
                        <div className="p-3">
                            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                                {card.name || 'LifeStore product'}
                            </p>
                            {card.price && <p className="mt-1 text-sm font-bold text-orange-600">{card.price}</p>}
                            {card.stock_status && <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{card.stock_status}</p>}
                        </div>
                    </a>
                ))}
            </div>
        </motion.div>
    );
};

const LifestoreVoiceAgentPage = () => {
    const navigate = useNavigate();
    const { theme, toggleTheme } = useTheme();
    const firstName = 'Guest';
    const initials = 'LS';

    const [phase, setPhase] = useState(PHASE.IDLE);
    const [isSpeaking, setIsSpeaking] = useState(false);
    const [isListening, setIsListening] = useState(false);
    const [transcript, setTranscript] = useState([]);
    const [statusText, setStatusText] = useState('Ready to connect');
    const [errorMessage, setErrorMessage] = useState('');
    const [provider, setProvider] = useState(null);
    const [showTranscript, setShowTranscript] = useState(false);
    const [productCards, setProductCards] = useState([]);
    const [checkoutOrderId, setCheckoutOrderId] = useState(null);

    const pcRef = useRef(null);
    const dcRef = useRef(null);
    const openaiAudioRef = useRef(null);
    const geminiWsRef = useRef(null);
    const audioContextRef = useRef(null);
    const scriptProcessorRef = useRef(null);
    const micStreamRef = useRef(null);
    const nextPlayTimeRef = useRef(0);
    const sessionTokenRef = useRef(null);
    const voiceThreadIdRef = useRef(createVoiceThreadId());

    useEffect(() => {
        if (transcript.length > 0) setShowTranscript(true);
    }, [transcript.length]);

    useEffect(() => {
        fetch(`${API_URL}${LIFESTORE_REALTIME_PATH}/provider`)
            .then((r) => r.json())
            .then((d) => setProvider(d.provider))
            .catch(() => setProvider('openai'));
    }, []);

    useEffect(() => {
        const root = document.documentElement;
        if (theme === 'dark') root.classList.add('dark');
        else root.classList.remove('dark');
        return () => root.classList.remove('dark');
    }, [theme]);

    const cleanupOpenAI = useCallback(() => {
        if (dcRef.current) { dcRef.current.close(); dcRef.current = null; }
        if (pcRef.current) { pcRef.current.close(); pcRef.current = null; }
        if (openaiAudioRef.current) openaiAudioRef.current.srcObject = null;
    }, []);

    const cleanupGemini = useCallback(() => {
        if (scriptProcessorRef.current) { scriptProcessorRef.current.disconnect(); scriptProcessorRef.current = null; }
        if (micStreamRef.current) { micStreamRef.current.getTracks().forEach((track) => track.stop()); micStreamRef.current = null; }
        if (geminiWsRef.current) { geminiWsRef.current.close(); geminiWsRef.current = null; }
        if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null; }
        nextPlayTimeRef.current = 0;
    }, []);

    const cleanupAll = useCallback(() => {
        cleanupOpenAI();
        cleanupGemini();
    }, [cleanupOpenAI, cleanupGemini]);

    useEffect(() => () => cleanupAll(), [cleanupAll]);

    const applyLifeStoreEvents = useCallback((events = []) => {
        for (const item of events) {
            if (!item || typeof item !== 'object') continue;
            if (item.type === 'product_cards' && Array.isArray(item.products)) {
                setProductCards(item.products);
            }
            if (item.type === 'checkout' && item.order_id) {
                setCheckoutOrderId(item.order_id);
            }
        }
    }, []);

    const sendOpenAIEvent = useCallback((event) => {
        if (dcRef.current?.readyState === 'open') {
            dcRef.current.send(JSON.stringify(event));
        }
    }, []);

    const configureOpenAISession = useCallback(() => {
        sendOpenAIEvent({
            type: 'session.update',
            session: {
                type: 'realtime',
                instructions: LIFESTORE_SYSTEM_PROMPT,
                output_modalities: ['audio'],
                audio: {
                    input: { turn_detection: { type: 'server_vad', threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 600 } },
                    output: { voice: 'alloy' },
                },
                tool_choice: 'auto',
                tools: [
                    {
                        type: 'function',
                        name: ASK_LIFESTORE_TOOL,
                        description: 'Send a LifeStore product, cart, or checkout request to the existing Ask LifeStore chat agent.',
                        parameters: {
                            type: 'object',
                            properties: {
                                message: { type: 'string' },
                            },
                            required: ['message'],
                        },
                    },
                ],
            },
        });
    }, [sendOpenAIEvent]);

    const handleOpenAIMessage = useCallback(async (event) => {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }

        switch (msg.type) {
            case 'session.created':
                configureOpenAISession();
                setStatusText('Speak to Ask LifeStore');
                setPhase(PHASE.CONNECTED);
                break;
            case 'response.audio.delta':
                setIsSpeaking(true);
                break;
            case 'response.audio.done':
                setIsSpeaking(false);
                break;
            case 'input_audio_buffer.speech_started':
                setIsListening(true);
                setStatusText('Listening...');
                break;
            case 'input_audio_buffer.speech_stopped':
                setIsListening(false);
                setStatusText('Processing...');
                break;
            case 'conversation.item.input_audio_transcription.completed':
                if (msg.transcript?.trim()) {
                    setTranscript((prev) => [...prev, { role: 'user', text: msg.transcript.trim() }]);
                    setStatusText('Speak to Ask LifeStore');
                }
                break;
            case 'response.audio_transcript.delta':
                setTranscript((prev) => {
                    const last = prev[prev.length - 1];
                    if (last?.role === 'assistant' && last?.partial) {
                        return [...prev.slice(0, -1), { ...last, text: last.text + msg.delta }];
                    }
                    return [...prev, { role: 'assistant', text: msg.delta, partial: true }];
                });
                break;
            case 'response.audio_transcript.done':
                setTranscript((prev) => {
                    const last = prev[prev.length - 1];
                    if (last?.role === 'assistant' && last?.partial) {
                        return [...prev.slice(0, -1), { role: 'assistant', text: last.text }];
                    }
                    return prev;
                });
                setIsSpeaking(false);
                setStatusText('Speak to Ask LifeStore');
                break;
            case 'response.function_call_arguments.done':
                if (msg.name === ASK_LIFESTORE_TOOL) {
                    try {
                        const args = JSON.parse(msg.arguments || '{}');
                        setStatusText('Checking LifeStore...');
                        const res = await fetch(`${API_URL}${LIFESTORE_REALTIME_PATH}/chat-tool`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                message: args.message || args.query || args.question || '',
                                thread_id: voiceThreadIdRef.current,
                                user_id: 'anonymous',
                            }),
                        });
                        const data = res.ok
                            ? await res.json()
                            : { answer: 'I could not reach Ask LifeStore right now.', events: [] };
                        applyLifeStoreEvents(data.events || []);
                        sendOpenAIEvent({
                            type: 'conversation.item.create',
                            item: {
                                type: 'function_call_output',
                                call_id: msg.call_id,
                                output: data.answer || 'I could not find that LifeStore information.',
                            },
                        });
                        sendOpenAIEvent({ type: 'response.create' });
                        setStatusText('Speak to Ask LifeStore');
                    } catch {
                        sendOpenAIEvent({
                            type: 'conversation.item.create',
                            item: {
                                type: 'function_call_output',
                                call_id: msg.call_id,
                                output: 'Ask LifeStore is unavailable right now.',
                            },
                        });
                        sendOpenAIEvent({ type: 'response.create' });
                    }
                }
                break;
            case 'error':
                setErrorMessage(msg.error?.message || 'An error occurred');
                setPhase(PHASE.ERROR);
                break;
            default:
                break;
        }
    }, [applyLifeStoreEvents, configureOpenAISession, sendOpenAIEvent]);

    const playGeminiAudioChunk = useCallback((base64Data) => {
        if (!audioContextRef.current) return;
        const ctx = audioContextRef.current;
        const float32 = pcm16Base64ToFloat32(base64Data);
        const buffer = ctx.createBuffer(1, float32.length, 24000);
        buffer.copyToChannel(float32, 0);
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        const startTime = Math.max(ctx.currentTime, nextPlayTimeRef.current);
        source.start(startTime);
        nextPlayTimeRef.current = startTime + buffer.duration;
        setIsSpeaking(true);
        source.onended = () => {
            if (nextPlayTimeRef.current <= ctx.currentTime) setIsSpeaking(false);
        };
    }, []);

    const getSessionToken = async () => null;

    const startConversation = async () => {
        setPhase(PHASE.CONNECTING);
        setStatusText('Connecting...');
        setTranscript([]);
        setErrorMessage('');
        setShowTranscript(false);
        setProductCards([]);
        setCheckoutOrderId(null);

        try {
            const sessionToken = await getSessionToken();
            const providerRes = await fetch(`${API_URL}${LIFESTORE_REALTIME_PATH}/provider`);
            const providerData = await providerRes.json();
            const activeProvider = providerData.provider;
            setProvider(activeProvider);

            if (activeProvider === 'gemini') await startGeminiSession(sessionToken);
            else await startOpenAISession(sessionToken);
        } catch (err) {
            setErrorMessage(err.message || 'Failed to start voice session');
            setPhase(PHASE.ERROR);
            cleanupAll();
        }
    };

    const startOpenAISession = async (sessionToken) => {
        sessionTokenRef.current = sessionToken;
        setStatusText('Getting session token...');

        const tokenRes = await fetch(`${API_URL}${LIFESTORE_REALTIME_PATH}/token`);
        if (!tokenRes.ok) throw new Error('Failed to get OpenAI token');
        const { value: ephemeralKey } = await tokenRes.json();
        if (!ephemeralKey) throw new Error('No ephemeral token returned');

        setStatusText('Setting up audio...');
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const pc = new RTCPeerConnection();
        pcRef.current = pc;

        if (!openaiAudioRef.current) {
            openaiAudioRef.current = document.createElement('audio');
            openaiAudioRef.current.autoplay = true;
            document.body.appendChild(openaiAudioRef.current);
        }
        pc.ontrack = (e) => { openaiAudioRef.current.srcObject = e.streams[0]; };
        stream.getTracks().forEach((track) => pc.addTrack(track, stream));

        const dc = pc.createDataChannel('oai-events');
        dcRef.current = dc;
        dc.onmessage = handleOpenAIMessage;
        dc.onerror = () => {
            setErrorMessage('Connection error. Please try again.');
            setPhase(PHASE.ERROR);
        };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        setStatusText('Negotiating connection...');

        const sdpRes = await fetch('https://api.openai.com/v1/realtime/calls?model=gpt-realtime-2', {
            method: 'POST',
            headers: { Authorization: `Bearer ${ephemeralKey}`, 'Content-Type': 'application/sdp' },
            body: offer.sdp,
        });
        if (!sdpRes.ok) throw new Error(`SDP failed: ${await sdpRes.text()}`);
        await pc.setRemoteDescription({ type: 'answer', sdp: await sdpRes.text() });
        setStatusText('Establishing voice channel...');
    };

    const startGeminiSession = async (sessionToken) => {
        setStatusText('Requesting microphone...');
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        micStreamRef.current = stream;

        const ctx = new AudioContext({ sampleRate: 16000 });
        audioContextRef.current = ctx;
        nextPlayTimeRef.current = ctx.currentTime;

        setStatusText('Connecting...');
        const ws = new WebSocket(`${WS_URL}${LIFESTORE_REALTIME_PATH}/ws/voice`);
        geminiWsRef.current = ws;

        await new Promise((resolve, reject) => {
            ws.onopen = resolve;
            ws.onerror = () => reject(new Error('WebSocket failed'));
            setTimeout(() => reject(new Error('Timeout')), 10000);
        });

        setStatusText('Setting up audio pipeline...');

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                switch (msg.type) {
                    case 'ready':
                        ws.send(JSON.stringify({
                            type: 'auth',
                            session_token: sessionToken,
                            user_id: 'anonymous',
                            thread_id: voiceThreadIdRef.current,
                        }));
                        setStatusText('Speak to Ask LifeStore');
                        setPhase(PHASE.CONNECTED);
                        startMicCapture(ctx, ws);
                        break;
                    case 'audio':
                        playGeminiAudioChunk(msg.data);
                        break;
                    case 'transcript':
                        if (msg.role === 'user') {
                            setIsListening(false);
                            setTranscript((prev) => [...prev, { role: 'user', text: msg.text }]);
                        } else {
                            setTranscript((prev) => {
                                const last = prev[prev.length - 1];
                                if (last?.role === 'assistant' && last?.partial) {
                                    return [...prev.slice(0, -1), { ...last, text: last.text + msg.text }];
                                }
                                return [...prev, { role: 'assistant', text: msg.text, partial: true }];
                            });
                        }
                        setStatusText('Speak to Ask LifeStore');
                        break;
                    case 'product_cards':
                        if (Array.isArray(msg.products)) setProductCards(msg.products);
                        break;
                    case 'checkout':
                        if (msg.order_id) setCheckoutOrderId(msg.order_id);
                        break;
                    case 'turn_complete':
                        setTranscript((prev) => {
                            const last = prev[prev.length - 1];
                            if (last?.role === 'assistant' && last?.partial) {
                                return [...prev.slice(0, -1), { role: 'assistant', text: last.text }];
                            }
                            return prev;
                        });
                        break;
                    case 'listening':
                        setIsListening(true);
                        setStatusText('Listening...');
                        break;
                    case 'session_end':
                        setStatusText('Session ended - click Start to reconnect');
                        setPhase(PHASE.IDLE);
                        cleanupGemini();
                        break;
                    case 'error':
                        setErrorMessage(msg.message || 'Connection error');
                        setPhase(PHASE.ERROR);
                        cleanupGemini();
                        break;
                    default:
                        break;
                }
            } catch {
                // Ignore malformed provider events.
            }
        };

        ws.onclose = () => {
            if (phase === PHASE.CONNECTED) {
                setStatusText('Call ended - click Start to reconnect');
                setPhase(PHASE.IDLE);
            }
        };
        ws.onerror = () => {
            setErrorMessage('Connection to voice backend failed');
            setPhase(PHASE.ERROR);
        };
    };

    const startMicCapture = (ctx, ws) => {
        const source = ctx.createMediaStreamSource(micStreamRef.current);
        const processor = ctx.createScriptProcessor(4096, 1, 1);
        scriptProcessorRef.current = processor;
        processor.onaudioprocess = (e) => {
            if (ws.readyState !== WebSocket.OPEN) return;
            ws.send(JSON.stringify({ type: 'audio', data: float32ToPcm16Base64(e.inputBuffer.getChannelData(0)) }));
        };
        source.connect(processor);
        processor.connect(ctx.destination);
    };

    const endConversation = useCallback(() => {
        if (geminiWsRef.current?.readyState === WebSocket.OPEN) {
            geminiWsRef.current.send(JSON.stringify({ type: 'end' }));
        }
        cleanupAll();
        setPhase(PHASE.IDLE);
        setIsSpeaking(false);
        setIsListening(false);
        setStatusText('Ready to connect');
    }, [cleanupAll]);

    const isActive = phase === PHASE.CONNECTED;
    const displayStatus = isListening ? 'Listening...' : isSpeaking ? 'Speaking...' : statusText;

    return (
        <div className="h-screen flex bg-[#fafafa] dark:bg-[#111318] text-gray-900 dark:text-gray-100 overflow-hidden">
            <div className="hidden sm:flex w-14 flex-shrink-0 flex-col items-center py-4 gap-2 border-r border-gray-200 dark:border-white/[0.06] bg-white dark:bg-[#0d0f14]">
                <div className="flex-1" />
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
                <div className="w-9 h-9 flex items-center justify-center rounded-full bg-gradient-to-br from-cyan-900 to-cyan-600 text-white text-xs font-bold shadow-md">
                    {initials}
                </div>
            </div>

            <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
                <div className="relative flex items-center justify-between px-6 py-3 border-b border-gray-200 dark:border-white/[0.06] shrink-0">
                    <motion.button
                        type="button"
                        onClick={() => { endConversation(); navigate('/asklifestore'); }}
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        title="Back to Ask LifeStore"
                        className="group flex items-center gap-2 pl-3 pr-4 sm:pl-4 sm:pr-5 py-2 rounded-full bg-gradient-to-r from-cyan-900 to-cyan-600 text-white text-sm font-semibold shadow-md hover:shadow-lg ring-1 ring-black/5 transition-all shrink-0"
                    >
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2.2} stroke="currentColor" className="w-4 h-4 transition-transform duration-300 ease-out group-hover:-translate-x-1">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
                        </svg>
                        <span className="hidden sm:inline">Ask LifeStore</span>
                    </motion.button>

                    <h1 className="absolute left-1/2 top-[calc(50%_+_3px)] -translate-x-1/2 -translate-y-1/2 text-lg sm:text-xl font-bold tracking-tight text-gray-950 dark:text-gray-100">LifeStore Voice Agent</h1>
                    <img src={sltLogo} alt="SLTMobitel" className="h-7 sm:h-10 w-auto object-contain opacity-90 dark:opacity-80" />
                </div>

                <div className="flex-1 flex flex-col items-center justify-center px-6 pb-6 min-h-0 gap-0 overflow-y-auto">
                    <AnimatePresence>
                        {phase === PHASE.IDLE && (
                            <motion.div
                                initial={{ opacity: 0, y: -8 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -8 }}
                                transition={{ duration: 0.3 }}
                                className="mb-6 text-center"
                            >
                                <p className="text-[0.75rem] uppercase tracking-[0.18em] font-semibold text-gray-400 dark:text-gray-600 mb-1">Welcome back</p>
                                <p className="text-xl font-semibold text-gray-800 dark:text-gray-200">{firstName}</p>
                            </motion.div>
                        )}
                    </AnimatePresence>

                    <VoiceOrb phase={phase} isSpeaking={isSpeaking} isListening={isListening} theme={theme} />

                    <div className="mt-4 h-6 flex items-center justify-center">
                        {isActive ? <ListeningWave isListening={isListening} isSpeaking={isSpeaking} /> : <div className="h-5" />}
                    </div>

                    <motion.p
                        key={displayStatus}
                        initial={{ opacity: 0, y: 3 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.2 }}
                        className={`mt-2 text-[0.78rem] font-medium tracking-wide ${
                            phase === PHASE.ERROR ? 'text-red-500 dark:text-red-400'
                            : isListening ? 'text-teal-600 dark:text-teal-400'
                            : isSpeaking ? 'text-cyan-600 dark:text-cyan-400'
                            : phase === PHASE.CONNECTING ? 'text-gray-500'
                            : phase === PHASE.CONNECTED ? 'text-gray-500 dark:text-gray-400'
                            : 'text-gray-400 dark:text-gray-600'
                        }`}
                    >
                        {displayStatus}
                    </motion.p>

                    {phase === PHASE.ERROR && errorMessage && (
                        <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-2 text-xs text-red-500/70 dark:text-red-400/60 text-center max-w-xs leading-relaxed">
                            {errorMessage}
                        </motion.p>
                    )}

                    <div className="mt-8">
                        <CallControls phase={phase} onStart={startConversation} onEnd={endConversation} />
                    </div>

                    <ProductCardsPanel cards={productCards} onClear={() => setProductCards([])} />

                    {checkoutOrderId && (
                        <div className="w-full max-w-xl mt-5">
                            <div className="mb-2 flex justify-end">
                                <button
                                    type="button"
                                    onClick={() => {
                                        setCheckoutOrderId(null);
                                        setStatusText('Checkout closed');
                                    }}
                                    className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-900 dark:border-white/[0.08] dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white"
                                >
                                    Close checkout
                                </button>
                            </div>
                            <LifestoreCheckout orderId={checkoutOrderId} />
                        </div>
                    )}

                    <AnimatePresence>
                        {showTranscript && transcript.length > 0 && (
                            <TranscriptPanel
                                transcript={transcript}
                                onClear={() => { setTranscript([]); setShowTranscript(false); }}
                            />
                        )}
                    </AnimatePresence>
                </div>

                <div className="w-full flex items-center justify-center gap-1.5 pb-3 pt-1 shrink-0 select-none cursor-default">
                    <span className="text-[0.65rem] uppercase tracking-wider font-semibold text-gray-500 dark:text-gray-400">Powered by</span>
                    <img src={embryoLogo} alt="Embryo Logo" className="h-[20px] w-auto object-contain dark:brightness-110" />
                </div>
            </div>
        </div>
    );
};

export default LifestoreVoiceAgentPage;
