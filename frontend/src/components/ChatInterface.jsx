import React, { useState, useRef, useEffect } from 'react';
import { useMsal } from "@azure/msal-react";
import { motion } from 'framer-motion';
import { v4 as uuidv4 } from 'uuid';
import LifestoreForm from './forms/LifestoreForm';
import EnterpriseForm from './forms/EnterpriseForm';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import VoiceRecorder from './voice/VoiceRecorder';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// Generative UI trigger tokens emitted by the backend
const FORM_TOKENS = {
    '[RENDER_LIFESTORE_FORM]': 'lifestore',
    '[RENDER_ENTERPRISE_FORM]': 'enterprise',
};

// Strip unmatched ** bold markers so stray asterisks don't render literally.
const sanitizeMarkdownBold = (text) => {
    if (!text) return text;
    const positions = [];
    const regex = /\*\*/g;
    let m;
    while ((m = regex.exec(text)) !== null) positions.push(m.index);
    if (positions.length % 2 === 0) return text;
    const last = positions[positions.length - 1];
    return text.slice(0, last) + text.slice(last + 2);
};

// Utility function to append incoming text chunks to the current message text
const appendChunkSmartly = (current, incoming) => {
    return (current || "") + (incoming || "");
};

// ── Source UI Components ───────

const SourceBadge = ({ name, url, color }) => (
    <motion.a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        whileHover={{ scale: 1.05, y: -2 }}
        whileTap={{ scale: 0.95 }}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-gray-100 shadow-sm transition-all hover:shadow-md hover:border-gray-200 group`}
    >
        <div className={`p-1 rounded-full bg-gradient-to-br ${color} text-white`}>
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3">
                <path d="M3 3.5A1.5 1.5 0 014.5 2h6.879a1.5 1.5 0 011.06.44l4.122 4.12A1.5 1.5 0 0117 7.622V16.5a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 013 16.5v-13z" />
            </svg>
        </div>
        <span className="text-xs font-medium text-gray-600 group-hover:text-gray-900 truncate max-w-[150px]">
            {name}
        </span>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3 text-gray-300 group-hover:text-gray-500">
            <path fillRule="evenodd" d="M5.22 14.78a.75.75 0 001.06 0l7.22-7.22v5.69a.75.75 0 001.5 0v-7.5a.75.75 0 00-.75-.75h-7.5a.75.75 0 000 1.5h5.69l-7.22 7.22a.75.75 0 000 1.06z" clipRule="evenodd" />
        </svg>
    </motion.a>
);

const SourcesSection = ({ sources, color }) => {
    if (!sources || sources.length === 0) return null;
    return (
        <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-4 pt-3 border-t border-gray-100/60"
        >
            <div className="flex items-center gap-2 mb-2.5">
                <div className={`w-1 h-3.5 rounded-full bg-gradient-to-b ${color}`} />
                <span className="text-[0.7rem] uppercase tracking-wider font-bold text-gray-400">Sources</span>
            </div>
            <div className="flex flex-wrap gap-2">
                {sources.map((src, i) => (
                    <SourceBadge key={i} name={src.name} url={src.url} color={color} />
                ))}
            </div>
        </motion.div>
    );
};

// ── Voice Playback bar — inside user voice message bubble ─────
const VoicePlayback = ({ audioUrl, duration }) => {
    const [playing, setPlaying] = useState(false);
    const audioRef = useRef(null);

    const toggle = () => {
        if (!audioRef.current) {
            audioRef.current = new Audio(audioUrl);
            audioRef.current.onended = () => setPlaying(false);
        }
        if (playing) {
            audioRef.current.pause();
            setPlaying(false);
        } else {
            audioRef.current.play();
            setPlaying(true);
        }
    };

    const fmt = (s) =>
        `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`;

    return (
        <div className="flex items-center gap-2 mb-2 bg-white/20 rounded-xl px-3 py-2">
            <button onClick={toggle} className="text-white/90 hover:text-white transition-colors shrink-0">
                {playing ? (
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                        <path d="M5.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75A.75.75 0 007.25 3h-1.5zM12.75 3a.75.75 0 00-.75.75v12.5c0 .414.336.75.75.75h1.5a.75.75 0 00.75-.75V3.75a.75.75 0 00-.75-.75h-1.5z" />
                    </svg>
                ) : (
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                        <path d="M6.3 2.841A1.5 1.5 0 004 4.11V15.89a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z" />
                    </svg>
                )}
            </button>
            <div className="flex items-center gap-[2px] h-5">
                {[3, 6, 9, 5, 8, 4, 7, 5, 3, 6, 4, 8].map((h, i) => (
                    <div key={i} className="w-[3px] rounded-full bg-white/60" style={{ height: h }} />
                ))}
            </div>
            <span className="text-white/70 text-xs font-mono tabular-nums">{fmt(duration || 0)}</span>
        </div>
    );
};

// ── Feedback Buttons Component ─
const FeedbackButtons = ({ messageIndex, agentId, threadId, userId, existingRating, onFeedback }) => {
    const [rating, setRating] = useState(existingRating || null);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        setRating(existingRating || null);
    }, [existingRating]);

    const handleFeedback = async (newRating) => {
        if (submitting) return;
        const finalRating = rating === newRating ? null : newRating;
        setSubmitting(true);
        try {
            if (!finalRating) {
                const res = await fetch(`${API_URL}/api/v1/feedback`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        agent_id: agentId,
                        thread_id: threadId,
                        message_index: messageIndex,
                        rating: newRating,
                        user_id: userId,
                    }),
                });
                if (res.ok) {
                    setRating(null);
                    onFeedback?.(messageIndex, null);
                }
            } else {
                const res = await fetch(`${API_URL}/api/v1/feedback`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        agent_id: agentId,
                        thread_id: threadId,
                        message_index: messageIndex,
                        rating: finalRating,
                        user_id: userId,
                    }),
                });
                if (res.ok) {
                    setRating(finalRating);
                    onFeedback?.(messageIndex, finalRating);
                }
            }
        } catch (err) {
            console.error('Feedback submission failed:', err);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="flex items-center gap-2">
            <button
                onClick={() => handleFeedback('up')}
                disabled={submitting}
                className={`p-1.5 rounded-md transition-all duration-200 ${
                    rating === 'up'
                        ? 'text-emerald-500 bg-emerald-50'
                        : 'text-gray-300 hover:text-emerald-400 hover:bg-emerald-50/50'
                }`}
                title="Helpful"
            >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M1 8.998a1 1 0 011-1h.764a1.483 1.483 0 00-.076.506v5.996a1.483 1.483 0 00.076.506H2a1 1 0 01-1-1V8.998zM5.25 7.726a2 2 0 01.944-1.697l3.476-2.14a1.5 1.5 0 012.33 1.25v2.363h2.5a2 2 0 011.96 2.4l-.782 3.908A2 2 0 0113.72 15.5H5.25V7.726z" />
                </svg>
            </button>
            <button
                onClick={() => handleFeedback('down')}
                disabled={submitting}
                className={`p-1.5 rounded-md transition-all duration-200 ${
                    rating === 'down'
                        ? 'text-red-400 bg-red-50'
                        : 'text-gray-300 hover:text-red-400 hover:bg-red-50/50'
                }`}
                title="Not helpful"
            >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M19 11.002a1 1 0 01-1 1h-.764a1.483 1.483 0 00.076-.506V5.5a1.483 1.483 0 00-.076-.506H18a1 1 0 011 1v5.008zM14.75 12.274a2 2 0 01-.944 1.697l-3.476 2.14a1.5 1.5 0 01-2.33-1.25V12.5h-2.5a2 2 0 01-1.96-2.4l.782-3.908A2 2 0 016.28 4.5h8.47v7.774z" />
                </svg>
            </button>
        </div>
    );
};

// ── Main ChatInterface Component ──────────────────────────────
const ChatInterface = ({ agentConfig }) => {
    const { accounts } = useMsal();
    const user = accounts[0] || { name: "User" };

    // ── Existing state ─────────
    const [threadId, setThreadId] = useState('');
    const [messages, setMessages] = useState([]);
    const [isLoadingHistory, setIsLoadingHistory] = useState(false);
    const [feedbackMap, setFeedbackMap] = useState({});
    const [input, setInput] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const messagesEndRef = useRef(null);

    // ── Voice input state ──────
    const [voiceMode, setVoiceMode] = useState(false);

    // ── Voice output (TTS) state ──────────────────────────────
    const [speakingIndex, setSpeakingIndex] = useState(null);
    const currentAudioRef = useRef(null);

    // ── TTS helpers ────────────
    const stopSpeaking = () => {
        if (currentAudioRef.current) {
            currentAudioRef.current.pause();
            currentAudioRef.current = null;
        }
        setSpeakingIndex(null);
    };

    const handleSpeak = async (text, index) => {
        // Toggle off if same message clicked
        if (speakingIndex === index) {
            stopSpeaking();
            return;
        }
        stopSpeaking();

        // Strip markdown before speaking
        const cleanText = text
            .replace(/\*\*(.*?)\*\*/g, '$1')
            .replace(/\*(.*?)\*/g, '$1')
            .replace(/#{1,6}\s/g, '')
            .replace(/\*{0,2}Sources:\*{0,2}[\s\S]*/i, '')
            .replace(/\[.*?\]\(.*?\)/g, '')
            .trim();

        if (!cleanText) return;
        setSpeakingIndex(index);

        try {
            const formData = new FormData();
            formData.append('text', cleanText);
            formData.append('voice', 'alloy');

            const response = await fetch(`${API_URL}/api/v1/voice/tts`, {
                method: 'POST',
                body: formData,
            });
            if (!response.ok) throw new Error('TTS failed');

            const audioBlob = await response.blob();
            const audioUrl = URL.createObjectURL(audioBlob);
            const audio = new Audio(audioUrl);
            currentAudioRef.current = audio;

            audio.onended = () => {
                setSpeakingIndex(null);
                currentAudioRef.current = null;
                URL.revokeObjectURL(audioUrl);
            };
            audio.onerror = () => {
                setSpeakingIndex(null);
                currentAudioRef.current = null;
            };

            await audio.play();
        } catch (err) {
            console.error('TTS error:', err);
            setSpeakingIndex(null);
        }
    };

    // ── Agent switching ────────
    useEffect(() => {
        if (!agentConfig?.id) return;

        setThreadId('');
        setMessages([]);
        setFeedbackMap({});
        setIsLoadingHistory(true);
        setVoiceMode(false);
        stopSpeaking();

        const loadAgentState = async () => {
            const storageKey = `thread_${agentConfig.id}`;
            const storedThreadId = sessionStorage.getItem(storageKey);
            const isExistingSession = !!storedThreadId;

            const currentThreadId = storedThreadId || uuidv4();
            if (!isExistingSession) {
                sessionStorage.setItem(storageKey, currentThreadId);
            }

            setThreadId(currentThreadId);

            if (isExistingSession) {
                try {
                    const response = await fetch(`${API_URL}/api/v1/chat/${agentConfig.id}/${currentThreadId}`);
                    if (!response.ok) throw new Error("Failed to fetch history");

                    const data = await response.json();
                    if (data.messages && data.messages.length > 0) {
                        const mappedMessages = data.messages.map(msg => {
                            let text = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
                            let formType = null;
                            for (const [token, type] of Object.entries(FORM_TOKENS)) {
                                if (text.includes(token)) {
                                    formType = type;
                                    text = text.replace(token, '').trim();
                                    break;
                                }
                            }
                            return {
                                type: msg.type === 'human' ? 'user' : 'bot',
                                text,
                                formType,
                            };
                        });
                        setMessages(mappedMessages);

                        try {
                            const fbRes = await fetch(`${API_URL}/api/v1/feedback/${agentConfig.id}/${currentThreadId}`);
                            if (fbRes.ok) {
                                const fbData = await fbRes.json();
                                const userId = user.username || "anonymous";
                                const map = {};
                                for (const [idx, users] of Object.entries(fbData.feedback || {})) {
                                    if (users[userId]) {
                                        map[idx] = users[userId];
                                    }
                                }
                                setFeedbackMap(map);
                            }
                        } catch (fbErr) {
                            console.error("Error fetching feedback:", fbErr);
                        }
                    } else {
                        setMessages([{
                            type: 'bot',
                            text: `Hello ${user.name.split(" ")[0]}! I am your ${agentConfig.title} assistant. How can I help you today?`
                        }]);
                    }
                } catch (error) {
                    console.error("Error fetching history:", error);
                    setMessages([{
                        type: 'bot',
                        text: `Welcome back! I had trouble retrieving our last conversation, but I'm ready to help.`
                    }]);
                }
            } else {
                setMessages([{
                    type: 'bot',
                    text: `Hello ${user.name.split(" ")[0]}! I am your ${agentConfig.title} assistant. How can I help you today?`
                }]);
            }

            setIsLoadingHistory(false);
        };

        loadAgentState();
    }, [agentConfig.id, agentConfig.title, user.name]);

    // ── Auto-scroll ────────────
    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    // ── Voice send (STT → chat) 
    const handleVoiceSend = async (audioBlob, durationSeconds) => {
        setVoiceMode(false);
        if (!threadId || isLoadingHistory) return;

        const audioUrl = URL.createObjectURL(audioBlob);

        setMessages(prev => [...prev, {
            type: 'user',
            text: '🎤 Transcribing...',
            audioUrl: null,
            audioDuration: durationSeconds,
            isTranscribing: true,
        }]);
        setIsLoading(true);

        try {
            // Step 1 — Whisper STT
            const formData = new FormData();
            const ext = audioBlob.type.includes('mp4') ? 'mp4' : 'webm';
            formData.append('audio', audioBlob, `recording.${ext}`);
            formData.append('language', 'en');

            const sttRes = await fetch(`${API_URL}/api/v1/voice/stt`, {
                method: 'POST',
                body: formData,
            });
            if (!sttRes.ok) throw new Error('STT failed');
            const { text: transcribedText } = await sttRes.json();

            // Step 2 — Replace transcribing bubble with real voice bubble
            setMessages(prev => {
                const updated = [...prev];
                updated[updated.length - 1] = {
                    type: 'user',
                    text: transcribedText,
                    audioUrl,
                    audioDuration: durationSeconds,
                    isTranscribing: false,
                };
                return updated;
            });

            // Step 3 — Send transcribed text to agent
            const response = await fetch(`${API_URL}/api/v1/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: transcribedText,
                    agent_id: agentConfig.id,
                    user_id: user.username || "anonymous",
                    thread_id: threadId,
                }),
            });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

            // Step 4 — Stream response
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulatedText = "";
            setMessages(prev => [...prev, { type: 'bot', text: "", formType: null }]);

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true }).replace(/\r/g, '');
                accumulatedText = appendChunkSmartly(accumulatedText, chunk);

                let currentFormType = null;
                let cleanText = accumulatedText;
                for (const [token, type] of Object.entries(FORM_TOKENS)) {
                    if (cleanText.includes(token)) {
                        currentFormType = type;
                        cleanText = cleanText.replace(token, '').trim();
                        break;
                    }
                }
                setMessages(prev => {
                    const newMessages = [...prev];
                    const lastIdx = newMessages.length - 1;
                    newMessages[lastIdx] = {
                        ...newMessages[lastIdx],
                        text: cleanText,
                        formType: currentFormType || newMessages[lastIdx].formType,
                    };
                    return newMessages;
                });
            }

        } catch (err) {
            console.error('Voice send error:', err);
            setMessages(prev => [...prev, {
                type: 'bot',
                text: "Sorry, I couldn't process your voice message. Please try again.",
            }]);
        } finally {
            setIsLoading(false);
        }
    };

    // ── Text send ──────────────
    const handleSend = async (e) => {
        e.preventDefault();
        if (!input.trim() || !threadId || isLoadingHistory) return;

        const userMessage = { type: 'user', text: input };
        setMessages(prev => [...prev, userMessage]);
        setInput("");
        setIsLoading(true);

        try {
            const response = await fetch(`${API_URL}/api/v1/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: userMessage.text,
                    agent_id: agentConfig.id,
                    user_id: user.username || "anonymous",
                    thread_id: threadId
                })
            });

            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulatedText = "";

            setMessages(prev => [...prev, { type: 'bot', text: "", formType: null }]);

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, { stream: true }).replace(/\r/g, '');
                accumulatedText = appendChunkSmartly(accumulatedText, chunk);

                let currentFormType = null;
                let cleanText = accumulatedText;
                for (const [token, type] of Object.entries(FORM_TOKENS)) {
                    if (cleanText.includes(token)) {
                        currentFormType = type;
                        cleanText = cleanText.replace(token, '').trim();
                        break;
                    }
                }

                setMessages(prev => {
                    const newMessages = [...prev];
                    const lastIdx = newMessages.length - 1;
                    newMessages[lastIdx] = {
                        ...newMessages[lastIdx],
                        text: cleanText,
                        formType: currentFormType || newMessages[lastIdx].formType
                    };
                    return newMessages;
                });
            }

        } catch (error) {
            console.error("Error:", error);
            setMessages(prev => [...prev, { type: 'bot', text: "Sorry, I'm having trouble connecting to the server. Is the backend running?" }]);
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1], delay: 0.1 }}
            className="flex-1 flex flex-col w-full max-w-[1250px] mx-auto px-4 z-10 pt-6 pb-0 min-h-0 overflow-hidden"
        >
            {/* Title Section */}
            <motion.div
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.2 }}
                className="text-center mb-4 space-y-2"
            >
                <h1 className="text-5xl sm:text-6xl font-extrabold text-white tracking-tight drop-shadow-lg uppercase">{agentConfig.title}</h1>
                <p className="text-white/70 text-sm sm:text-base mx-auto font-light whitespace-nowrap overflow-hidden text-ellipsis">{agentConfig.subtitle}</p>
            </motion.div>

            {/* ── Premium Chat Workspace ── */}
            <motion.div
                initial={{ opacity: 0, y: 25, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: 0.3 }}
                className="relative flex-1 mb-4 sm:mb-8 min-h-0 rounded-2xl sm:rounded-3xl z-10"
            >
                {/* Ambient glow */}
                <div className={`absolute -inset-2 blur-[30px] opacity-30 bg-gradient-to-br ${agentConfig.color} rounded-[2.5rem] -z-10 transition-colors duration-700 pointer-events-none`} />

                {/* Glass card */}
                <div className="relative bg-[#fbfcff] w-full h-full rounded-2xl sm:rounded-3xl border border-white/80 shadow-[0_20px_50px_-10px_rgba(0,0,0,0.2),inset_0_1px_1px_rgba(255,255,255,1)] flex flex-col overflow-hidden">

                    {/* Messages Area */}
                    <div className="flex-1 flex flex-col relative z-0 pt-3 sm:pt-5 min-h-0">
                        <div className="flex-1 overflow-y-auto px-6 sm:px-8 space-y-5 chat-scrollbar min-h-0 relative transform-gpu will-change-transform">

                            {isLoadingHistory && (
                                <div className="absolute inset-0 bg-white/50 backdrop-blur-sm z-10 flex items-center justify-center">
                                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900"></div>
                                </div>
                            )}

                            {messages.map((msg, index) => (
                                (msg.type === 'user' || msg.text || msg.formType) && (
                                    <motion.div
                                        key={index}
                                        initial={{ opacity: 0, y: 10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        transition={{ duration: 0.35, ease: 'easeOut' }}
                                        className={`flex ${msg.type === 'user' ? 'justify-end' : 'justify-start'}`}
                                    >
                                        {/* Chatbubble */}
                                        <div className={`max-w-[80%] sm:max-w-[75%] rounded-2xl px-5 sm:px-6 py-3.5 sm:py-4 text-[0.9375rem] leading-relaxed shadow-sm ${
                                            msg.type === 'user'
                                                ? `bg-gradient-to-br ${agentConfig.color} text-white rounded-tr-md`
                                                : 'bg-white/95 border border-gray-100/60 text-gray-700 rounded-tl-md'
                                        }`}>

                                            {/* Voice playback bar — only on voice messages */}
                                            {msg.type === 'user' && msg.audioUrl && !msg.isTranscribing && (
                                                <VoicePlayback
                                                    audioUrl={msg.audioUrl}
                                                    duration={msg.audioDuration}
                                                />
                                            )}

                                            {/* Message text */}
                                            <div className="prose prose-sm max-w-none text-inherit">
                                                {(() => {
                                                    const parts = msg.text.split(/\*{0,2}Sources:\*{0,2}/);
                                                    const mainText = parts[0].replace(/\s*\*+\s*$/, "").trimEnd();
                                                    const sourcesPart = parts.length > 1 ? parts.slice(1).join("") : "";
                                                    const sourceMatches = sourcesPart.matchAll(/\[(.*?)\]\((.*?)\)/g);
                                                    const sources = Array.from(sourceMatches).map(m => ({ name: m[1], url: m[2] }));

                                                    return (
                                                        <>
                                                            <ReactMarkdown
                                                                remarkPlugins={[remarkGfm]}
                                                                components={{
                                                                    p: ({ node, ...props }) => <p className="mb-2 last:mb-0" {...props} />,
                                                                    a: ({ node, ...props }) => <a className="text-blue-500 hover:underline" target="_blank" rel="noopener noreferrer" {...props} />,
                                                                    ul: ({ node, ...props }) => <ul className="list-disc pl-4 mb-2 space-y-1" {...props} />,
                                                                    ol: ({ node, ...prefix }) => <ol className="list-decimal pl-4 mb-2 space-y-1" {...prefix} />,
                                                                    li: ({ node, ...props }) => <li className="pl-1" {...props} />,
                                                                    table: ({ node, ...props }) => (
                                                                        <div className="overflow-x-auto my-4 rounded-lg border border-gray-200 bg-white">
                                                                            <table className="w-full text-sm text-left border-collapse" {...props} />
                                                                        </div>
                                                                    ),
                                                                    th: ({ node, ...props }) => <th className="bg-gray-50 px-4 py-2 font-semibold border-b border-gray-200 text-gray-700 border-r last:border-r-0" {...props} />,
                                                                    td: ({ node, ...props }) => <td className="px-4 py-2 border-b border-gray-100 border-r border-gray-100 last:border-r-0 text-gray-600" {...props} />,
                                                                    tr: ({ node, ...props }) => <tr className="even:bg-gray-50/50 hover:bg-gray-50 transition-colors" {...props} />,
                                                                    code: ({ node, inline, className, children, ...props }) => {
                                                                        return inline ? (
                                                                            <code className="bg-white border border-gray-100 shadow-sm px-1.5 py-0.5 rounded text-sm font-mono text-pink-600" {...props}>
                                                                                {children}
                                                                            </code>
                                                                        ) : (
                                                                            <code className="block bg-gray-50 p-3 rounded-xl text-sm font-mono overflow-x-auto my-2 border border-gray-100 shadow-inner text-gray-700" {...props}>
                                                                                {children}
                                                                            </code>
                                                                        );
                                                                    }
                                                                }}
                                                            >
                                                                {sanitizeMarkdownBold(mainText)}
                                                            </ReactMarkdown>
                                                            {msg.type === 'bot' && (
                                                                <SourcesSection sources={sources} color={agentConfig.color} />
                                                            )}
                                                        </>
                                                    );
                                                })()}
                                            </div>

                                            {/* Generative UI forms */}
                                            {msg.formType === 'lifestore' && <LifestoreForm />}
                                            {msg.formType === 'enterprise' && <EnterpriseForm />}

                                            {/* ── Feedback + Speaker buttons — bot messages only ── */}
                                            {msg.type === 'bot' && index > 0 && msg.text && !isLoading && (
                                                <div className="flex items-center justify-between mt-2 -mb-1">
                                                    {/* Existing feedback buttons */}
                                                    <FeedbackButtons
                                                        messageIndex={index}
                                                        agentId={agentConfig.id}
                                                        threadId={threadId}
                                                        userId={user.username || "anonymous"}
                                                        existingRating={feedbackMap[index] || null}
                                                        onFeedback={(idx, rating) => setFeedbackMap(prev => ({ ...prev, [idx]: rating }))}
                                                    />

                                                    {/* Speaker / TTS button */}
                                                    <button
                                                        onClick={() => handleSpeak(msg.text, index)}
                                                        title={speakingIndex === index ? "Stop reading" : "Read aloud"}
                                                        className={`p-1.5 rounded-md transition-all duration-200 ${
                                                            speakingIndex === index
                                                                ? 'text-blue-500 bg-blue-50'
                                                                : 'text-gray-300 hover:text-blue-400 hover:bg-blue-50/50'
                                                        }`}
                                                    >
                                                        {speakingIndex === index ? (
                                                            /* Animated speaker — playing */
                                                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                                                                <path d="M10 3.75a.75.75 0 00-1.264-.546L4.703 7H3.167a.75.75 0 00-.7.48A6.985 6.985 0 002 10c0 .887.165 1.737.468 2.52.111.29.39.48.7.48h1.535l4.033 3.796A.75.75 0 0010 16.25V3.75zM15.95 5.05a.75.75 0 00-1.06 1.061 5.5 5.5 0 010 7.778.75.75 0 001.06 1.06 7 7 0 000-9.899z" />
                                                                <path d="M13.829 7.172a.75.75 0 00-1.061 1.06 2.5 2.5 0 010 3.536.75.75 0 001.06 1.06 4 4 0 000-5.656z" />
                                                            </svg>
                                                        ) : (
                                                            /* Static speaker — idle */
                                                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                                                                <path d="M10 3.75a.75.75 0 00-1.264-.546L4.703 7H3.167a.75.75 0 00-.7.48A6.985 6.985 0 002 10c0 .887.165 1.737.468 2.52.111.29.39.48.7.48h1.535l4.033 3.796A.75.75 0 0010 16.25V3.75z" />
                                                            </svg>
                                                        )}
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    </motion.div>
                                )
                            ))}

                            {/* Typing indicator */}
                            {isLoading && (messages.length === 0 || messages[messages.length - 1].type === 'user' || (!messages[messages.length - 1].text && !messages[messages.length - 1].formType)) && (
                                <div className="flex justify-start">
                                    <div className="bg-gray-50/80 backdrop-blur-md border border-gray-100/60 rounded-2xl rounded-tl-md px-6 py-4 shadow-sm flex gap-1.5 items-center">
                                        <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce" />
                                        <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce [animation-delay:150ms]" />
                                        <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce [animation-delay:300ms]" />
                                    </div>
                                </div>
                            )}
                            <div ref={messagesEndRef} className="h-1 sm:h-2" />
                        </div>

                        {/* Fog veil */}
                        <div className="absolute bottom-0 left-0 right-4 h-10 sm:h-14 bg-gradient-to-t from-[#fbfcff] via-[#fbfcff]/80 to-transparent pointer-events-none z-10" />
                    </div>

                    {/* ── DOCKED INPUT AREA ── */}
                    <div className="w-full px-2 sm:px-6 pb-1.5 pt-0.5 bg-[#fbfcff] z-20 flex flex-col justify-end border-t border-gray-50/50">

                        {/* Voice mode: VoiceRecorder replaces the form */}
                        {voiceMode ? (
                            <VoiceRecorder
                                agentColor={agentConfig.color}
                                onSend={handleVoiceSend}
                                onCancel={() => setVoiceMode(false)}
                            />
                        ) : (
                            <form onSubmit={handleSend} className="relative flex items-center w-full pointer-events-auto group">
                                <div className="relative flex items-center w-full bg-[#fbfcff]/95 backdrop-blur-3xl rounded-full border border-gray-200/80 shadow-[0_12px_40px_-10px_rgba(0,0,0,0.1),inset_0_2px_4px_rgba(255,255,255,1)] p-0.5 focus-within:shadow-[0_12px_40px_-10px_rgba(0,0,0,0.15)] focus-within:ring-2 focus-within:ring-gray-200/50 transition-shadow">

                                    <input
                                        type="text"
                                        value={input}
                                        onChange={(e) => setInput(e.target.value)}
                                        placeholder={`${agentConfig.title} anything...`}
                                        className="flex-1 bg-transparent text-gray-800 placeholder:text-gray-400 text-[0.9375rem] pl-4 py-1.5 sm:py-[0.375rem] outline-none"
                                    />

                                    {/* Mic button */}
                                    <button
                                        type="button"
                                        onClick={() => setVoiceMode(true)}
                                        disabled={isLoading || !threadId || isLoadingHistory}
                                        title="Voice message"
                                        className="relative p-1.5 rounded-full text-gray-400 hover:text-gray-600 hover:bg-black/5 transition-all duration-300 flex items-center justify-center shrink-0 ml-1 disabled:opacity-40"
                                    >
                                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-[1.125rem] h-[1.125rem] sm:w-5 sm:h-5">
                                            <path d="M8.25 4.5a3.75 3.75 0 117.5 0v8.25a3.75 3.75 0 11-7.5 0V4.5z" />
                                            <path d="M6 10.5a.75.75 0 01.75.75v1.5a5.25 5.25 0 1010.5 0v-1.5a.75.75 0 011.5 0v1.5a6.751 6.751 0 01-6 6.709v2.291h3a.75.75 0 010 1.5h-7.5a.75.75 0 010-1.5h3v-2.291a6.751 6.751 0 01-6-6.709v-1.5A.75.75 0 016 10.5z" />
                                        </svg>
                                    </button>

                                    {/* Send button — unchanged */}
                                    <button
                                        type="submit"
                                        disabled={!input.trim() || isLoading || !threadId || isLoadingHistory}
                                        className={`relative p-1.5 rounded-full transition-all duration-300 flex items-center justify-center shrink-0 ml-1.5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.4)]
                                            ${input.trim()
                                                ? `bg-gradient-to-tr ${agentConfig.color} text-white shadow-md hover:shadow-lg hover:scale-105`
                                                : 'bg-black/5 text-gray-400 hover:text-gray-600 hover:bg-black/10'
                                            } disabled:opacity-40 disabled:hover:scale-100 disabled:shadow-none`}
                                    >
                                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-[1.125rem] h-[1.125rem] sm:w-5 sm:h-5">
                                            <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                                        </svg>
                                    </button>
                                </div>
                            </form>
                        )}

                        <p className="text-center text-[0.65rem] text-gray-400/80 mt-1 font-light pointer-events-auto">
                            {agentConfig.disclaimer}
                        </p>
                    </div>
                </div>
            </motion.div>
        </motion.div>
    );
};

export default ChatInterface;