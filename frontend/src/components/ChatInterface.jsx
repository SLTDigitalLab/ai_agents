import React, { useState, useRef, useEffect, useLayoutEffect, useMemo, forwardRef, useImperativeHandle } from 'react';
import { useMsal } from "@azure/msal-react";
import { motion, AnimatePresence } from 'framer-motion';
import { v4 as uuidv4 } from 'uuid';
import LifestoreForm from './forms/LifestoreForm';
import EnterpriseForm from './forms/EnterpriseForm';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// Generative UI trigger tokens emitted by the backend
const FORM_TOKENS = {
    '[RENDER_LIFESTORE_FORM]': 'lifestore',
    '[RENDER_ENTERPRISE_FORM]': 'enterprise',
};

// Greeting pool for the idle landing screen.
const TIME_GREETINGS = {
    morning: ["Good morning, {n}", "Rise and shine, {n}", "Morning, {n}"],
    afternoon: ["Good afternoon, {n}", "Hi, {n}", "Hey, {n}"],
    evening: ["Good evening, {n}", "Evening, {n}", "Hi, {n}"],
    night: ["Burning the midnight oil, {n}?", "Still up, {n}?", "Late night, {n}?"],
};
const ANYTIME_GREETINGS = [
    "Welcome back, {n}",
    "Back at it, {n}",
    "{n} returns",
    "Ready when you are, {n}",
    "Hello, {n}",
    "Hi, {n}",
    "Hello again, {n}",
];
const pickGreeting = (firstName) => {
    const hour = new Date().getHours();
    let bucket;
    if (hour >= 5 && hour < 12) bucket = 'morning';
    else if (hour >= 12 && hour < 17) bucket = 'afternoon';
    else if (hour >= 17 && hour < 22) bucket = 'evening';
    else bucket = 'night';
    const pool = [...TIME_GREETINGS[bucket], ...ANYTIME_GREETINGS];
    const picked = pool[Math.floor(Math.random() * pool.length)];
    return picked.replace('{n}', firstName);
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

// Agent gradient class → RGB triplet, for luminous glow effects.
const AGENT_RGB = {
    cyan: '6, 182, 212', purple: '147, 51, 234', blue: '37, 99, 235',
    gray: '107, 114, 128', sky: '14, 165, 233', rose: '225, 29, 72',
    emerald: '16, 185, 129', indigo: '79, 70, 229', orange: '234, 88, 12',
    fuchsia: '192, 38, 211',
};
const getAgentRgb = (colorStr) => {
    const m = colorStr?.match(/from-(\w+)-/);
    return (m && AGENT_RGB[m[1]]) || '120, 120, 120';
};

const formatTime = (ts) => {
    if (!ts) return '';
    try {
        return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return '';
    }
};

// Rotating status phrases shown while awaiting the first streamed token.
const THINKING_PHRASES = [
    "Understanding your question",
    "Reviewing relevant information",
    "Analyzing details",
    "Consulting knowledge base",
    "Preparing your response",
    "Finalizing answer",
];
const PHRASE_INTERVAL_MS = 2200;

const ThinkingIndicator = () => {
    const [phraseIdx, setPhraseIdx] = useState(0);

    useEffect(() => {
        const id = setInterval(() => {
            setPhraseIdx(prev => Math.min(prev + 1, THINKING_PHRASES.length - 1));
        }, PHRASE_INTERVAL_MS);
        return () => clearInterval(id);
    }, []);

    return (
        <div className="flex justify-start">
            <div className="bg-gray-50/80 dark:bg-gray-800/60 backdrop-blur-md border border-gray-100/60 dark:border-gray-700/60 rounded-2xl rounded-tl-md px-6 py-4 shadow-sm flex gap-3 items-center">
                <div className="flex gap-1.5 items-center">
                    <div className="w-2 h-2 rounded-full bg-gray-300 dark:bg-gray-600 animate-bounce" />
                    <div className="w-2 h-2 rounded-full bg-gray-300 dark:bg-gray-600 animate-bounce [animation-delay:150ms]" />
                    <div className="w-2 h-2 rounded-full bg-gray-300 dark:bg-gray-600 animate-bounce [animation-delay:300ms]" />
                </div>
                <AnimatePresence mode="wait">
                    <motion.span
                        key={phraseIdx}
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -4 }}
                        transition={{ duration: 0.2, ease: 'easeOut' }}
                        className="text-sm text-gray-500 dark:text-gray-400 font-light"
                    >
                        {THINKING_PHRASES[phraseIdx]}
                    </motion.span>
                </AnimatePresence>
            </div>
        </div>
    );
};

// ── Source UI Components ──────────────────────────────────────

const SourceBadge = ({ name, url, color }) => (
    <motion.a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        whileHover={{ scale: 1.05, y: -2 }}
        whileTap={{ scale: 0.95 }}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-full bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 shadow-sm transition-all hover:shadow-md hover:border-gray-200 dark:hover:border-gray-600 group`}
    >
        <div className={`p-1 rounded-full bg-gradient-to-br ${color} text-white`}>
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3">
                <path d="M3 3.5A1.5 1.5 0 014.5 2h6.879a1.5 1.5 0 011.06.44l4.122 4.12A1.5 1.5 0 0117 7.622V16.5a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 013 16.5v-13z" />
            </svg>
        </div>
        <span className="text-xs font-medium text-gray-600 dark:text-gray-300 group-hover:text-gray-900 dark:group-hover:text-gray-100 truncate max-w-[150px]">
            {name}
        </span>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3 text-gray-300 dark:text-gray-600 group-hover:text-gray-500 dark:group-hover:text-gray-400">
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
            className="mt-4 pt-3 border-t border-gray-100/60 dark:border-gray-800/60"
        >
            <div className="flex items-center gap-2 mb-2.5">
                <div className={`w-1 h-3.5 rounded-full bg-gradient-to-b ${color}`} />
                <span className="text-[0.7rem] uppercase tracking-wider font-bold text-gray-400 dark:text-gray-500">Sources</span>
            </div>
            <div className="flex flex-wrap gap-2">
                {sources.map((src, i) => (
                    <SourceBadge key={i} name={src.name} url={src.url} color={color} />
                ))}
            </div>
        </motion.div>
    );
};

// ── Copy-to-clipboard helper used by message and code-block buttons ─────────
const useCopy = () => {
    const [copied, setCopied] = useState(false);
    const timeoutRef = useRef(null);
    const copy = async (text) => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
            timeoutRef.current = setTimeout(() => setCopied(false), 1500);
        } catch (err) {
            console.error('Copy failed:', err);
        }
    };
    useEffect(() => () => { if (timeoutRef.current) clearTimeout(timeoutRef.current); }, []);
    return { copied, copy };
};

// ── Copy Message Button (for bot bubbles) ──────────────────────────────────
const CopyMessageButton = ({ text }) => {
    const { copied, copy } = useCopy();
    return (
        <button
            type="button"
            onClick={() => copy(text)}
            disabled={copied}
            title={copied ? "Copied" : "Copy message"}
            className={`p-1.5 rounded-md transition-all duration-200 ${copied
                    ? 'text-emerald-500 bg-emerald-50 dark:bg-emerald-500/10'
                    : 'text-gray-300 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100/60 dark:hover:bg-gray-800/60'
                }`}
        >
            {copied ? (
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
                </svg>
            ) : (
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M7 3a2 2 0 012-2h6a2 2 0 012 2v6a2 2 0 01-2 2h-1V7a3 3 0 00-3-3H7V3z" />
                    <path d="M3 7a2 2 0 012-2h6a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                </svg>
            )}
        </button>
    );
};

// ── Code Block with Copy button (used inside ReactMarkdown) ────────────────
const CodeBlock = ({ children, ...props }) => {
    const text = React.Children.toArray(children).map(c => typeof c === 'string' ? c : (c?.props?.children ?? '')).join('');
    const { copied, copy } = useCopy();
    return (
        <div className="relative group/code my-2">
            <button
                type="button"
                onClick={() => copy(text)}
                title={copied ? "Copied" : "Copy code"}
                className={`absolute top-2 right-2 px-2 py-1 rounded-md text-[0.7rem] font-medium transition-all duration-200 opacity-0 group-hover/code:opacity-100 ${copied
                        ? 'bg-emerald-500 text-white'
                        : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-100 hover:border-gray-300 dark:hover:border-gray-600 shadow-sm'
                    }`}
            >
                {copied ? 'Copied' : 'Copy'}
            </button>
            <code className="block bg-gray-50 dark:bg-gray-900 p-3 pr-16 rounded-xl text-sm font-mono overflow-x-auto border border-gray-100 dark:border-gray-800 shadow-inner text-gray-700 dark:text-gray-200" {...props}>
                {children}
            </code>
        </div>
    );
};

// ── Feedback Buttons Component ──────────────────────────────────────
const FeedbackButtons = ({ messageIndex, agentId, threadId, userId, existingRating, onFeedback }) => {
    const [rating, setRating] = useState(existingRating || null);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        setRating(existingRating || null);
    }, [existingRating]);

    const handleFeedback = async (newRating) => {
        if (submitting) return;

        // Toggle off if same rating clicked
        const finalRating = rating === newRating ? null : newRating;

        setSubmitting(true);
        try {
            if (!finalRating) {
                // Remove feedback from database
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
                // Submit or update feedback
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
        <>
            <button
                onClick={() => handleFeedback('up')}
                disabled={submitting}
                className={`p-1.5 rounded-md transition-all duration-200 ${rating === 'up'
                        ? 'text-emerald-500 bg-emerald-50 dark:bg-emerald-500/10'
                        : 'text-gray-300 dark:text-gray-600 hover:text-emerald-400 hover:bg-emerald-50/50 dark:hover:bg-emerald-500/10'
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
                className={`p-1.5 rounded-md transition-all duration-200 ${rating === 'down'
                        ? 'text-red-400 bg-red-50 dark:bg-red-500/10'
                        : 'text-gray-300 dark:text-gray-600 hover:text-red-400 hover:bg-red-50/50 dark:hover:bg-red-500/10'
                    }`}
                title="Not helpful"
            >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M19 11.002a1 1 0 01-1 1h-.764a1.483 1.483 0 00.076-.506V5.5a1.483 1.483 0 00-.076-.506H18a1 1 0 011 1v5.008zM14.75 12.274a2 2 0 01-.944 1.697l-3.476 2.14a1.5 1.5 0 01-2.33-1.25V12.5h-2.5a2 2 0 01-1.96-2.4l.782-3.908A2 2 0 016.28 4.5h8.47v7.774z" />
                </svg>
            </button>
        </>
    );
};

// ── New Chat Button (Gemini-style pencil icon, expands to label on hover) ─
const NewChatButton = ({ onClick, disabled }) => (
    <motion.button
        onClick={onClick}
        disabled={disabled}
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.9 }}
        whileHover={{ scale: disabled ? 1 : 1.04 }}
        whileTap={{ scale: disabled ? 1 : 0.96 }}
        title="New chat"
        className="group/new flex items-center gap-1.5 pl-2 pr-2 py-1.5 rounded-full bg-white/90 backdrop-blur-md border border-gray-200/80 text-gray-500 hover:text-gray-800 hover:border-gray-300 shadow-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed text-xs font-medium overflow-hidden"
    >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
            <path d="M5.433 13.917l1.262-3.155A4 4 0 017.58 9.42l6.92-6.918a2.121 2.121 0 013 3l-6.92 6.918c-.383.383-.84.685-1.343.886l-3.154 1.262a.5.5 0 01-.65-.65z" />
            <path d="M3.5 5.75c0-.69.56-1.25 1.25-1.25H10A.75.75 0 0010 3H4.75A2.75 2.75 0 002 5.75v9.5A2.75 2.75 0 004.75 18h9.5A2.75 2.75 0 0017 15.25V10a.75.75 0 00-1.5 0v5.25c0 .69-.56 1.25-1.25 1.25h-9.5c-.69 0-1.25-.56-1.25-1.25v-9.5z" />
        </svg>
        <span className="max-w-0 group-hover/new:max-w-[100px] overflow-hidden whitespace-nowrap transition-all duration-300 ease-out">
            <span className="pr-1">New chat</span>
        </span>
    </motion.button>
);

// ── Scroll-to-latest pill ──────────────────────────────────────────────
const ScrollToLatestPill = ({ onClick, color }) => (
    <motion.button
        type="button"
        onClick={onClick}
        initial={{ opacity: 0, y: 10, scale: 0.9 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 10, scale: 0.9 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        title="Scroll to latest"
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-gradient-to-tr ${color} text-white text-xs font-medium shadow-lg hover:shadow-xl transition-shadow`}
    >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5">
            <path fillRule="evenodd" d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z" clipRule="evenodd" />
        </svg>
        Latest
    </motion.button>
);

const ChatInterface = forwardRef(({ agentConfig }, ref) => {
    const { accounts } = useMsal();
    const user = accounts[0] || { name: "User" };

    // State for thread ID and messages
    const [threadId, setThreadId] = useState('');
    const [messages, setMessages] = useState([]);
    const [isLoadingHistory, setIsLoadingHistory] = useState(false);
    const [feedbackMap, setFeedbackMap] = useState({}); // { messageIndex: rating }

    // Effect to handle Agent switching:
    // 1. Get/Create thread_id for the specific agent
    // 2. Load history if exists, else reset messages
    useEffect(() => {
        if (!agentConfig?.id) return;

        // ── CRITICAL: Immediately clear stale state to prevent race conditions ──
        setThreadId('');
        setMessages([]);
        setFeedbackMap({});
        setIsLoadingHistory(true);

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
                        // Empty history — fall through to the idle landing screen.
                        setMessages([]);
                    }
                } catch (error) {
                    console.error("Error fetching history:", error);
                    // On fetch error, still show the idle screen rather than a canned greeting.
                    setMessages([]);
                }
            } else {
                // Fresh session — show the idle landing screen.
                setMessages([]);
            }

            setIsLoadingHistory(false);
        };

        loadAgentState();
    }, [agentConfig.id, agentConfig.title, user.name]);

    const [input, setInput] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [lastFailedMessage, setLastFailedMessage] = useState(null);
    const [latestVisible, setLatestVisible] = useState(true);
    const messagesEndRef = useRef(null);
    const scrollContainerRef = useRef(null);
    const latestUserMsgRef = useRef(null);
    const lastMessageRef = useRef(null);
    const inputRef = useRef(null);
    const abortControllerRef = useRef(null);
    const [containerHeight, setContainerHeight] = useState(0);
    // Spacer below the latest exchange — sized to allow the latest user message
    // to anchor at the viewport top while NOT permitting scroll past the bot reply.
    const [bottomSpacer, setBottomSpacer] = useState(0);

    // True when no exchange has happened yet (only the seeded greeting message exists).
    // Declared here so the observer effects below can react to mode changes.
    const isIdleForEffects = messages.length <= 1 && !isLoadingHistory && !isLoading;

    // Track scroll container height — needed by the bottom spacer to guarantee
    // enough room for the latest user message to anchor at the viewport top.
    // Re-binds when transitioning from idle to chat mode (scroll container only mounts then).
    useEffect(() => {
        const el = scrollContainerRef.current;
        if (!el) return;
        const update = () => setContainerHeight(el.clientHeight);
        update();
        const ro = new ResizeObserver(update);
        ro.observe(el);
        return () => ro.disconnect();
    }, [isIdleForEffects]);

    // Observe the last rendered message; pill appears when it scrolls out of view.
    // Re-binds when message count changes OR when the last item transitions from
    // empty placeholder to having content (briefly happens during stream startup).
    const lastTextPresent = !!messages[messages.length - 1]?.text;
    useEffect(() => {
        const target = lastMessageRef.current;
        const root = scrollContainerRef.current;
        if (!target || !root) return;
        const obs = new IntersectionObserver(
            ([entry]) => setLatestVisible(entry.isIntersecting),
            { root, threshold: 0.1 }
        );
        obs.observe(target);
        return () => obs.disconnect();
    }, [messages.length, lastTextPresent]);

    // Re-measure the bottom spacer whenever the latest exchange grows.
    // Formula: containerHeight - (top of latest user msg → bottom of last msg) - 32px buffer.
    // If the exchange fully fills the viewport, spacer is 0 — no scroll past the bot reply.
    useEffect(() => {
        const userEl = latestUserMsgRef.current;
        const lastEl = lastMessageRef.current;
        if (!userEl || !lastEl || containerHeight === 0) {
            setBottomSpacer(0);
            return;
        }
        const compute = () => {
            const userRect = userEl.getBoundingClientRect();
            const lastRect = lastEl.getBoundingClientRect();
            const exchangeHeight = lastRect.bottom - userRect.top;
            const next = Math.max(0, containerHeight - exchangeHeight - 32);
            setBottomSpacer(next);
        };
        compute();
        const ro = new ResizeObserver(compute);
        ro.observe(userEl);
        ro.observe(lastEl);
        return () => ro.disconnect();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [containerHeight, messages.length, lastTextPresent]);

    // Anchor the latest user message to the top of the scroll container.
    // Instant scrollTop set — no smooth animation — to eliminate any chance
    // of interruption. We can re-add smoothness later if this works.
    const anchorLatestUserToTop = () => {
        const el = latestUserMsgRef.current;
        const container = scrollContainerRef.current;
        if (!el || !container) {
            console.log('[anchor] aborted: missing ref', { el: !!el, container: !!container });
            return;
        }
        const elRect = el.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();
        const targetScrollTop = Math.max(0, container.scrollTop + (elRect.top - containerRect.top) - 16);
        const maxScroll = container.scrollHeight - container.clientHeight;
        console.log('[anchor] scroll', {
            currentScrollTop: container.scrollTop,
            elTopInViewport: elRect.top,
            containerTopInViewport: containerRect.top,
            targetScrollTop,
            maxScroll,
            scrollHeight: container.scrollHeight,
            clientHeight: container.clientHeight,
        });
        container.scrollTop = targetScrollTop;
    };

    const scrollToLatest = () => {
        const el = lastMessageRef.current;
        if (!el) return;
        el.scrollIntoView({ behavior: 'smooth', block: 'end' });
    };

    // After history loads, jump to bottom and focus input.
    useEffect(() => {
        if (!isLoadingHistory && messages.length > 0) {
            const c = scrollContainerRef.current;
            if (c) c.scrollTop = c.scrollHeight;
            setTimeout(() => inputRef.current?.focus(), 100);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isLoadingHistory]);

    // Index of the latest user message — used for anchor ref.
    const latestUserIdx = (() => {
        for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].type === 'user') return i;
        }
        return -1;
    })();

    // Flag set in sendMessage when the user just sent a message. Anchored via
    // useLayoutEffect (runs synchronously after DOM commit, before paint) once
    // chat mode is mounted and the scroll container is measured. Using a ref
    // (not state) keeps the flag stable across re-renders between send → mode
    // transition → container measurement.
    const anchorPendingRef = useRef(false);
    useLayoutEffect(() => {
        console.log('[anchor-effect] running', {
            pending: anchorPendingRef.current,
            isIdle: isIdleForEffects,
            containerHeight,
            latestUserIdx,
            hasUserRef: !!latestUserMsgRef.current,
            hasContainerRef: !!scrollContainerRef.current,
        });
        if (!anchorPendingRef.current) return;
        if (isIdleForEffects) return;          // wait for chat mode
        if (containerHeight === 0) return;     // wait for measurement
        if (!latestUserMsgRef.current) return; // wait for ref to attach
        anchorPendingRef.current = false;
        anchorLatestUserToTop();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [latestUserIdx, isIdleForEffects, containerHeight]);

    const handleClearChat = () => {
        if (!agentConfig?.id || isLoading || isLoadingHistory) return;
        const newThreadId = uuidv4();
        sessionStorage.setItem(`thread_${agentConfig.id}`, newThreadId);
        setThreadId(newThreadId);
        setFeedbackMap({});
        setLastFailedMessage(null);
        setMessages([]);
        setTimeout(() => inputRef.current?.focus(), 100);
    };

    // Expose imperative actions to the parent (Sidebar uses this for "New chat")
    useImperativeHandle(ref, () => ({
        clearChat: handleClearChat,
    }));

    const sendMessage = async (text) => {
        if (!text.trim() || !threadId || isLoadingHistory || isLoading) return;

        const userMessage = { type: 'user', text, timestamp: Date.now() };
        setMessages(prev => [...prev, userMessage]);
        setIsLoading(true);
        setLastFailedMessage(null);
        anchorPendingRef.current = true; // anchor effect will fire once chat mode is ready
        console.log('[anchor-pending] set', { text: text.slice(0, 30) });

        const controller = new AbortController();
        abortControllerRef.current = controller;
        let botMessageAdded = false;

        try {
            const response = await fetch(`${API_URL}/api/v1/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    agent_id: agentConfig.id,
                    user_id: user.username || "anonymous",
                    thread_id: threadId
                }),
                signal: controller.signal,
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulatedText = "";

            setMessages(prev => [...prev, { type: 'bot', text: "", formType: null, timestamp: Date.now() }]);
            botMessageAdded = true;

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
            if (error.name === 'AbortError') {
                // User stopped intentionally — partial response remains as-is.
            } else {
                console.error("Error:", error);
                setLastFailedMessage(text);
                if (botMessageAdded) {
                    // Mark the partial bot message as errored so retry shows.
                    setMessages(prev => {
                        const newMessages = [...prev];
                        const lastIdx = newMessages.length - 1;
                        newMessages[lastIdx] = { ...newMessages[lastIdx], error: true };
                        return newMessages;
                    });
                } else {
                    setMessages(prev => [...prev, {
                        type: 'bot',
                        text: "Sorry, I'm having trouble connecting to the server. Is the backend running?",
                        error: true,
                        timestamp: Date.now()
                    }]);
                }
            }
        } finally {
            setIsLoading(false);
            abortControllerRef.current = null;
            setTimeout(() => inputRef.current?.focus(), 100);
        }
    };

    const handleSend = (e) => {
        e.preventDefault();
        if (!input.trim()) return;
        const text = input;
        setInput("");
        // Reset textarea height after clearing
        if (inputRef.current) inputRef.current.style.height = 'auto';
        sendMessage(text);
    };

    const handleStop = () => {
        abortControllerRef.current?.abort();
    };

    const handleRetry = () => {
        if (lastFailedMessage && !isLoading) {
            sendMessage(lastFailedMessage);
        }
    };

    // Textarea auto-grow on input
    const handleInputChange = (e) => {
        setInput(e.target.value);
        const el = e.target;
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 150) + 'px';
    };

    // Enter sends, Shift+Enter inserts newline. Skip while IME composing (CJK input).
    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent?.isComposing) {
            e.preventDefault();
            handleSend(e);
        }
    };

    // Last RENDERED message index — filters out empty bot placeholders so the
    // scroll-to-latest observer and streaming cursor target a real DOM node.
    const lastRenderedIdx = (() => {
        for (let i = messages.length - 1; i >= 0; i--) {
            const m = messages[i];
            if (m.type === 'user' || m.text || m.formType) return i;
        }
        return -1;
    })();

    const firstName = (user.name || "User").split(" ")[0];
    const isIdle = isIdleForEffects;

    // Pick a fresh greeting whenever we (re-)enter idle, change agent, or start a new thread.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    const idleGreeting = useMemo(() => pickGreeting(firstName), [isIdle, agentConfig.id, firstName, threadId]);

    // ── Reusable Composer JSX ─────────────────────────────────────────────
    // `accent` (idle only) wraps the input in a gradient border ring + soft
    // ambient halo in the agent's color — premium "floating" look.
    const renderComposer = (accent = false) => {
        const controls = (
            <>
                <textarea
                    ref={inputRef}
                    rows={1}
                    value={input}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    placeholder="Ask anything..."
                    className="flex-1 bg-transparent text-gray-800 dark:text-gray-100 placeholder:text-gray-400 dark:placeholder:text-gray-500 text-[0.9375rem] pl-3 pr-2 py-2 outline-none resize-none leading-relaxed max-h-[150px] overflow-y-auto chat-scrollbar"
                />
                <button
                    type={isLoading ? "button" : "submit"}
                    onClick={isLoading ? handleStop : undefined}
                    disabled={!isLoading && (!input.trim() || !threadId || isLoadingHistory)}
                    title={isLoading ? "Stop generating" : "Send"}
                    className={`relative p-2 rounded-full transition-all duration-300 flex items-center justify-center shrink-0 ml-1.5
                        ${isLoading
                            ? 'bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 hover:bg-gray-800 dark:hover:bg-white shadow-md'
                            : input.trim()
                                ? `bg-gradient-to-tr ${agentConfig.color} text-white shadow-md hover:shadow-lg hover:scale-105`
                                : 'bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700'
                        } disabled:opacity-40 disabled:hover:scale-100 disabled:shadow-none`}
                >
                    {isLoading ? (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-[1.125rem] h-[1.125rem] sm:w-5 sm:h-5">
                            <rect x="6" y="6" width="12" height="12" rx="2" />
                        </svg>
                    ) : (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-[1.125rem] h-[1.125rem] sm:w-5 sm:h-5">
                            <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                        </svg>
                    )}
                </button>
            </>
        );

        if (accent) {
            const rgb = getAgentRgb(agentConfig.color);
            return (
                <form onSubmit={handleSend} className="relative flex w-full pointer-events-auto">
                    {/* Wide soft ambient halo radiating outward */}
                    <div
                        aria-hidden="true"
                        className={`absolute -inset-3 rounded-[2.4rem] bg-gradient-to-r ${agentConfig.color} opacity-[0.10] dark:opacity-20 blur-2xl pointer-events-none`}
                    />
                    {/* Input box with a luminous multi-layer colored glow edge */}
                    <div
                        className="relative flex items-end w-full bg-white dark:bg-[#23262c] rounded-3xl p-1.5"
                        style={{
                            boxShadow: `0 0 0 1px rgba(${rgb}, 0.22), 0 0 14px -3px rgba(${rgb}, 0.25), 0 0 38px -8px rgba(${rgb}, 0.18), 0 16px 50px -16px rgba(0, 0, 0, 0.22)`,
                        }}
                    >
                        {controls}
                    </div>
                </form>
            );
        }

        return (
            <form onSubmit={handleSend} className="relative flex items-end w-full pointer-events-auto">
                <div className="relative flex items-end w-full bg-white dark:bg-[#2a2e36] rounded-3xl border border-gray-200 dark:border-[#3a3f48] shadow-[0_8px_30px_-10px_rgba(0,0,0,0.08)] dark:shadow-[0_8px_30px_-10px_rgba(0,0,0,0.5)] p-1.5 focus-within:border-gray-300 dark:focus-within:border-[#4a505a] focus-within:shadow-[0_12px_40px_-10px_rgba(0,0,0,0.12)] dark:focus-within:shadow-[0_12px_40px_-10px_rgba(0,0,0,0.7)] transition-shadow">
                    {controls}
                </div>
            </form>
        );
    };

    const disclaimerText = (
        <p className="text-center text-[0.65rem] text-gray-400 dark:text-gray-500 mt-2 font-light px-2">
            {agentConfig.disclaimer}
        </p>
    );

    return (
        <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className="flex-1 flex flex-col min-h-0 w-full overflow-hidden z-10"
        >
            {isIdle ? (
                // ── IDLE LANDING SCREEN ─────────────────────────────────
                <motion.div
                    key="idle"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.35, ease: 'easeOut' }}
                    className="flex-1 flex flex-col items-center justify-center px-4 pb-24"
                >
                    <motion.h2
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.45, delay: 0.05 }}
                        className="text-3xl sm:text-4xl md:text-5xl font-semibold text-gray-900 dark:text-gray-100 tracking-tight text-center"
                    >
                        {idleGreeting}
                    </motion.h2>
                    <motion.p
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.45, delay: 0.12 }}
                        className="text-xl sm:text-2xl text-gray-500 dark:text-gray-400 mt-2 font-light text-center"
                    >
                        {agentConfig.idlePrompt || "How can I help you today?"}
                    </motion.p>
                    <motion.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.5, delay: 0.2 }}
                        className="w-full max-w-[720px] mt-10"
                    >
                        {renderComposer(true)}
                        {disclaimerText}
                    </motion.div>
                </motion.div>
            ) : (
                // ── CHAT MODE ───────────────────────────────────────────
                <motion.div
                    key="chat"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.3 }}
                    className="flex-1 flex flex-col min-h-0 w-full"
                >
                    {/* Messages area — scroll spans full width (scrollbar at page right edge) */}
                    <div className="relative flex-1 w-full min-h-0 flex flex-col">
                        {isLoadingHistory && (
                            <div className="absolute inset-0 bg-[#fafafa]/70 dark:bg-[#1c1f24]/70 backdrop-blur-sm z-30 flex items-center justify-center">
                                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-700 dark:border-gray-300"></div>
                            </div>
                        )}
                        <div
                            ref={scrollContainerRef}
                            style={{ overflowAnchor: 'none' }}
                            className="flex-1 overflow-y-auto chat-scrollbar min-h-0"
                        >
                            <div className="w-full max-w-[820px] mx-auto px-4 sm:px-6 space-y-7 py-6 pt-12">
                                {messages.map((msg, index) => {
                                    if (!(msg.type === 'user' || msg.text || msg.formType)) return null;
                                    const isLastMsg = index === lastRenderedIdx;
                                    const isStreamingThisMsg = isLoading && isLastMsg && msg.type === 'bot' && !msg.error;
                                    const isErrorMsg = msg.error && isLastMsg;

                                    const setRefs = (el) => {
                                        if (index === latestUserIdx) latestUserMsgRef.current = el;
                                        if (isLastMsg) lastMessageRef.current = el;
                                    };

                                    const parts = msg.text.split(/\*{0,2}Sources:\*{0,2}/);
                                    const mainText = parts[0].replace(/\s*\*+\s*$/, "").trimEnd();
                                    const sourcesPart = parts.length > 1 ? parts.slice(1).join("") : "";
                                    const sourceMatches = sourcesPart.matchAll(/\[(.*?)\]\((.*?)\)/g);
                                    const sources = Array.from(sourceMatches).map(m => ({ name: m[1], url: m[2] }));

                                    const markdownComponents = {
                                        p: ({ node, ...props }) => <p className="mb-3 last:mb-0" {...props} />,
                                        a: ({ node, ...props }) => <a className="text-blue-600 dark:text-blue-400 hover:underline" target="_blank" rel="noopener noreferrer" {...props} />,
                                        ul: ({ node, ...props }) => <ul className="list-disc pl-5 mb-3 space-y-1.5 marker:text-gray-400 dark:marker:text-gray-500" {...props} />,
                                        ol: ({ node, ...prefix }) => <ol className="list-decimal pl-5 mb-3 space-y-1.5 marker:text-gray-400 dark:marker:text-gray-500" {...prefix} />,
                                        li: ({ node, ...props }) => <li className="pl-1" {...props} />,
                                        h1: ({ node, ...props }) => <h1 className="text-xl font-semibold mt-4 mb-2" {...props} />,
                                        h2: ({ node, ...props }) => <h2 className="text-lg font-semibold mt-4 mb-2" {...props} />,
                                        h3: ({ node, ...props }) => <h3 className="text-base font-semibold mt-3 mb-2" {...props} />,
                                        strong: ({ node, ...props }) => <strong className="font-semibold text-gray-900 dark:text-gray-100" {...props} />,
                                        table: ({ node, ...props }) => (
                                            <div className="overflow-x-auto my-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
                                                <table className="w-full text-sm text-left border-collapse" {...props} />
                                            </div>
                                        ),
                                        th: ({ node, ...props }) => <th className="bg-gray-50 dark:bg-gray-800 px-4 py-2 font-semibold border-b border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 border-r dark:border-r-gray-700 last:border-r-0" {...props} />,
                                        td: ({ node, ...props }) => <td className="px-4 py-2 border-b border-gray-100 dark:border-gray-800 border-r border-gray-100 dark:border-r-gray-800 last:border-r-0 text-gray-600 dark:text-gray-300" {...props} />,
                                        tr: ({ node, ...props }) => <tr className="even:bg-gray-50/50 dark:even:bg-gray-800/40 hover:bg-gray-50 dark:hover:bg-gray-800/60 transition-colors" {...props} />,
                                        code: ({ node, inline, className, children, ...props }) => {
                                            if (inline) {
                                                return (
                                                    <code className="bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 px-1.5 py-0.5 rounded text-[0.85em] font-mono text-pink-600 dark:text-pink-400" {...props}>
                                                        {children}
                                                    </code>
                                                );
                                            }
                                            return <CodeBlock {...props}>{children}</CodeBlock>;
                                        }
                                    };

                                    return (
                                        <motion.div
                                            key={index}
                                            ref={setRefs}
                                            initial={{ opacity: 0, y: 10 }}
                                            animate={{ opacity: 1, y: 0 }}
                                            transition={{ duration: 0.35, ease: 'easeOut' }}
                                            style={msg.type === 'user' ? { scrollMarginTop: '16px' } : undefined}
                                            className={`group/msg flex flex-col ${msg.type === 'user' ? 'items-end' : 'items-start'}`}
                                        >
                                            {msg.type === 'user' ? (
                                                // User: keep the pill with agent gradient
                                                <div className={`max-w-[85%] sm:max-w-[80%] rounded-2xl px-5 py-3 text-[0.9375rem] leading-relaxed shadow-sm bg-gradient-to-br ${agentConfig.color} text-white rounded-tr-md`}>
                                                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                                                        {sanitizeMarkdownBold(mainText)}
                                                    </ReactMarkdown>
                                                </div>
                                            ) : (
                                                // Bot: no card — text flows directly on the page background.
                                                <div className="w-full text-[15px] sm:text-[16px] leading-[1.75] text-gray-800 dark:text-gray-200">
                                                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                                                        {sanitizeMarkdownBold(mainText)}
                                                    </ReactMarkdown>
                                                    {isStreamingThisMsg && (
                                                        <span className="inline-block align-middle w-[3px] h-4 bg-gray-500/70 dark:bg-gray-300/70 ml-0.5 rounded-sm animate-pulse" />
                                                    )}
                                                    <SourcesSection sources={sources} color={agentConfig.color} />
                                                    {msg.formType === 'lifestore' && <LifestoreForm />}
                                                    {msg.formType === 'enterprise' && <EnterpriseForm />}
                                                </div>
                                            )}

                                            {/* Action row (bot only) — below text on the page bg */}
                                            {msg.type === 'bot' && index > 0 && msg.text && !isStreamingThisMsg && (
                                                <div className="flex items-center gap-1 mt-1 -ml-1.5">
                                                    {!msg.error && (
                                                        <FeedbackButtons
                                                            messageIndex={index}
                                                            agentId={agentConfig.id}
                                                            threadId={threadId}
                                                            userId={user.username || "anonymous"}
                                                            existingRating={feedbackMap[index] || null}
                                                            onFeedback={(idx, rating) => setFeedbackMap(prev => ({ ...prev, [idx]: rating }))}
                                                        />
                                                    )}
                                                    {!msg.error && <CopyMessageButton text={msg.text} />}
                                                    {isErrorMsg && lastFailedMessage && (
                                                        <button
                                                            type="button"
                                                            onClick={handleRetry}
                                                            className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-md text-white bg-gradient-to-br ${agentConfig.color} hover:opacity-90 shadow-sm ml-1.5`}
                                                        >
                                                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5">
                                                                <path fillRule="evenodd" d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z" clipRule="evenodd" />
                                                            </svg>
                                                            Retry
                                                        </button>
                                                    )}
                                                </div>
                                            )}

                                            {msg.timestamp && (
                                                <span className={`text-[0.65rem] text-gray-400 dark:text-gray-500 mt-1 px-1 opacity-0 group-hover/msg:opacity-100 transition-opacity duration-200`}>
                                                    {formatTime(msg.timestamp)}
                                                </span>
                                            )}
                                        </motion.div>
                                    );
                                })}

                                {isLoading && (messages.length === 0 || messages[messages.length - 1].type === 'user' || (!messages[messages.length - 1].text && !messages[messages.length - 1].formType)) && (
                                    <ThinkingIndicator />
                                )}

                                {messages.length > 1 && bottomSpacer > 0 && (
                                    <div style={{ height: `${bottomSpacer}px` }} aria-hidden="true" />
                                )}
                                <div ref={messagesEndRef} className="h-2" />
                            </div>
                        </div>


                        {/* Scroll-to-latest pill */}
                        <div className="absolute bottom-3 left-0 right-0 flex justify-center pointer-events-none z-20">
                            <AnimatePresence>
                                {!latestVisible && messages.length > 1 && !isLoading && (
                                    <div className="pointer-events-auto">
                                        <ScrollToLatestPill onClick={scrollToLatest} color={agentConfig.color} />
                                    </div>
                                )}
                            </AnimatePresence>
                        </div>
                    </div>

                    {/* Docked composer — centered, content constrained */}
                    <div className="w-full flex flex-col items-center px-4 sm:px-6 pb-3 pt-1 z-20 shrink-0">
                        <div className="w-full max-w-[820px]">
                            {renderComposer()}
                            {disclaimerText}
                        </div>
                    </div>
                </motion.div>
            )}
        </motion.div>
    );
});

ChatInterface.displayName = 'ChatInterface';

export default ChatInterface;
