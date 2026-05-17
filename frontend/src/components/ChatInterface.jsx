import React, { useState, useRef, useEffect } from 'react';
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

// Generic fallback suggestions when an agent doesn't define its own.
const FALLBACK_SUGGESTIONS = [
    "What can you help me with?",
    "Show me an example",
    "How do I get started?",
];

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
            <div className="bg-gray-50/80 backdrop-blur-md border border-gray-100/60 rounded-2xl rounded-tl-md px-6 py-4 shadow-sm flex gap-3 items-center">
                <div className="flex gap-1.5 items-center">
                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce" />
                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce [animation-delay:150ms]" />
                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce [animation-delay:300ms]" />
                </div>
                <AnimatePresence mode="wait">
                    <motion.span
                        key={phraseIdx}
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -4 }}
                        transition={{ duration: 0.2, ease: 'easeOut' }}
                        className="text-sm text-gray-500 font-light"
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
            className={`p-1.5 rounded-md transition-all duration-200 ${
                copied ? 'text-emerald-500 bg-emerald-50' : 'text-gray-300 hover:text-gray-600 hover:bg-gray-100/60'
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
                className={`absolute top-2 right-2 px-2 py-1 rounded-md text-[0.7rem] font-medium transition-all duration-200 opacity-0 group-hover/code:opacity-100 ${
                    copied ? 'bg-emerald-500 text-white' : 'bg-white border border-gray-200 text-gray-500 hover:text-gray-800 hover:border-gray-300 shadow-sm'
                }`}
            >
                {copied ? 'Copied' : 'Copy'}
            </button>
            <code className="block bg-gray-50 p-3 pr-16 rounded-xl text-sm font-mono overflow-x-auto border border-gray-100 shadow-inner text-gray-700" {...props}>
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
        </>
    );
};

// ── Clear Chat Button ──────────────────────────────────────
const ClearChatButton = ({ onClick, disabled }) => (
    <motion.button
        onClick={onClick}
        disabled={disabled}
        initial={{ opacity: 0, y: -4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        whileHover={{ scale: disabled ? 1 : 1.04 }}
        whileTap={{ scale: disabled ? 1 : 0.96 }}
        title="Clear conversation"
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-white/80 hover:text-white hover:bg-white/20 hover:border-white/30 shadow-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed text-xs font-medium"
    >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5">
            <path fillRule="evenodd" d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z" clipRule="evenodd" />
        </svg>
        Clear chat
    </motion.button>
);

// ── Suggested Prompts (chips shown under the greeting) ───────────────────
const SuggestedPrompts = ({ prompts, onSelect, color }) => (
    <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.15 }}
        className="flex flex-wrap gap-2 mt-3 max-w-[80%] sm:max-w-[75%]"
    >
        {prompts.map((prompt, i) => (
            <motion.button
                key={i}
                type="button"
                onClick={() => onSelect(prompt)}
                whileHover={{ scale: 1.03, y: -1 }}
                whileTap={{ scale: 0.97 }}
                className={`text-left text-sm px-3.5 py-2 rounded-xl bg-white border border-gray-100 text-gray-600 hover:text-gray-900 hover:border-gray-200 hover:shadow-md shadow-sm transition-all`}
            >
                {prompt}
            </motion.button>
        ))}
    </motion.div>
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

const ChatInterface = ({ agentConfig }) => {
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

    // Track scroll container height — needed by the bottom spacer to guarantee
    // enough room for the latest user message to anchor at the viewport top.
    useEffect(() => {
        const el = scrollContainerRef.current;
        if (!el) return;
        const update = () => setContainerHeight(el.clientHeight);
        update();
        const ro = new ResizeObserver(update);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

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

    const anchorLatestUserToTop = () => {
        const el = latestUserMsgRef.current;
        const container = scrollContainerRef.current;
        if (!el || !container) return;
        const elRect = el.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();
        const target = container.scrollTop + (elRect.top - containerRect.top) - 12;
        container.scrollTo({ top: target, behavior: 'smooth' });
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

    const handleClearChat = () => {
        if (!agentConfig?.id || isLoading || isLoadingHistory) return;
        const newThreadId = uuidv4();
        sessionStorage.setItem(`thread_${agentConfig.id}`, newThreadId);
        setThreadId(newThreadId);
        setFeedbackMap({});
        setLastFailedMessage(null);
        setMessages([{
            type: 'bot',
            text: `Hello ${user.name.split(" ")[0]}! I am your ${agentConfig.title} assistant. How can I help you today?`
        }]);
        setTimeout(() => inputRef.current?.focus(), 100);
    };

    const sendMessage = async (text) => {
        if (!text.trim() || !threadId || isLoadingHistory || isLoading) return;

        const userMessage = { type: 'user', text, timestamp: Date.now() };
        setMessages(prev => [...prev, userMessage]);
        setIsLoading(true);
        setLastFailedMessage(null);

        // Anchor the new user message at the top of the viewport.
        requestAnimationFrame(() => {
            requestAnimationFrame(anchorLatestUserToTop);
        });

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

    const suggestions = agentConfig.suggestedPrompts || FALLBACK_SUGGESTIONS;
    const showSuggestions = messages.length === 1 && messages[0].type === 'bot' && !isLoadingHistory && !isLoading;
    // Last RENDERED message index — filters out empty bot placeholders so the
    // scroll-to-latest observer and streaming cursor target a real DOM node.
    const lastRenderedIdx = (() => {
        for (let i = messages.length - 1; i >= 0; i--) {
            const m = messages[i];
            if (m.type === 'user' || m.text || m.formType) return i;
        }
        return -1;
    })();

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

            {/* Clear Chat Button */}
            <div className="flex justify-end mb-2 min-h-[2rem]">
                <AnimatePresence>
                    {messages.length > 1 && !isLoadingHistory && (
                        <ClearChatButton onClick={handleClearChat} disabled={isLoading} />
                    )}
                </AnimatePresence>
            </div>

            {/* ── Premium Chat Workspace ─────────────────────── */}
            <motion.div
                initial={{ opacity: 0, y: 25, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: 0.3 }}
                className="relative flex-1 mb-4 sm:mb-8 min-h-0 rounded-2xl sm:rounded-3xl z-10"
            >
                <div className={`absolute -inset-2 blur-[30px] opacity-30 bg-gradient-to-br ${agentConfig.color} rounded-[2.5rem] -z-10 transition-colors duration-700 pointer-events-none`} />

                <div className="relative bg-[#fbfcff] w-full h-full rounded-2xl sm:rounded-3xl border border-white/80 shadow-[0_20px_50px_-10px_rgba(0,0,0,0.2),inset_0_1px_1px_rgba(255,255,255,1)] flex flex-col overflow-hidden">

                    <div className="flex-1 flex flex-col relative z-0 pt-3 sm:pt-5 min-h-0">
                        <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-6 sm:px-8 space-y-5 chat-scrollbar min-h-0 relative transform-gpu will-change-transform">
                            {isLoadingHistory && (
                                <div className="absolute inset-0 bg-white/50 backdrop-blur-sm z-10 flex items-center justify-center">
                                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900"></div>
                                </div>
                            )}
                            {messages.map((msg, index) => {
                                if (!(msg.type === 'user' || msg.text || msg.formType)) return null;
                                const isLastMsg = index === lastRenderedIdx;
                                const isStreamingThisMsg = isLoading && isLastMsg && msg.type === 'bot' && !msg.error;
                                const isErrorMsg = msg.error && isLastMsg;

                                // Compose refs: anchor for latest user, last-message observer target
                                const setRefs = (el) => {
                                    if (index === latestUserIdx) latestUserMsgRef.current = el;
                                    if (isLastMsg) lastMessageRef.current = el;
                                };

                                return (
                                    <motion.div
                                        key={index}
                                        ref={setRefs}
                                        initial={{ opacity: 0, y: 10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        transition={{ duration: 0.35, ease: 'easeOut' }}
                                        className={`group/msg flex flex-col ${msg.type === 'user' ? 'items-end' : 'items-start'}`}
                                    >
                                        <div className={`max-w-[80%] sm:max-w-[75%] rounded-2xl px-5 sm:px-6 py-3.5 sm:py-4 text-[0.9375rem] leading-relaxed shadow-sm ${msg.type === 'user'
                                            ? `bg-gradient-to-br ${agentConfig.color} text-white rounded-tr-md`
                                            : 'bg-white/95 border border-gray-100/60 text-gray-700 rounded-tl-md'
                                            }`}>
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
                                                                        if (inline) {
                                                                            return (
                                                                                <code className="bg-white border border-gray-100 shadow-sm px-1.5 py-0.5 rounded text-sm font-mono text-pink-600" {...props}>
                                                                                    {children}
                                                                                </code>
                                                                            );
                                                                        }
                                                                        return <CodeBlock {...props}>{children}</CodeBlock>;
                                                                    }
                                                                }}
                                                            >
                                                                {sanitizeMarkdownBold(mainText)}
                                                            </ReactMarkdown>
                                                            {isStreamingThisMsg && (
                                                                <span className="inline-block align-middle w-[3px] h-4 bg-gray-500/70 ml-0.5 rounded-sm animate-pulse" />
                                                            )}
                                                            {msg.type === 'bot' && (
                                                                <SourcesSection sources={sources} color={agentConfig.color} />
                                                            )}
                                                        </>
                                                    );
                                                })()}
                                            </div>

                                            {msg.formType === 'lifestore' && <LifestoreForm />}
                                            {msg.formType === 'enterprise' && <EnterpriseForm />}

                                            {/* Action row: feedback, copy, retry */}
                                            {msg.type === 'bot' && index > 0 && msg.text && !isStreamingThisMsg && (
                                                <div className="flex items-center gap-2 mt-2 -mb-1">
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
                                                            className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-md text-white bg-gradient-to-br ${agentConfig.color} hover:opacity-90 shadow-sm`}
                                                        >
                                                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3.5 h-3.5">
                                                                <path fillRule="evenodd" d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z" clipRule="evenodd" />
                                                            </svg>
                                                            Retry
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </div>

                                        {/* Hover timestamp (only for messages with a timestamp from this session) */}
                                        {msg.timestamp && (
                                            <span className={`text-[0.65rem] text-gray-400 mt-1 px-1 opacity-0 group-hover/msg:opacity-100 transition-opacity duration-200`}>
                                                {formatTime(msg.timestamp)}
                                            </span>
                                        )}
                                    </motion.div>
                                );
                            })}

                            {/* Suggested prompts under the greeting */}
                            {showSuggestions && (
                                <SuggestedPrompts prompts={suggestions} onSelect={sendMessage} color={agentConfig.color} />
                            )}

                            {isLoading && (messages.length === 0 || messages[messages.length - 1].type === 'user' || (!messages[messages.length - 1].text && !messages[messages.length - 1].formType)) && (
                                <ThinkingIndicator />
                            )}

                            {/* Bottom spacer — keeps the latest user message anchorable at the viewport top. */}
                            {messages.length > 1 && containerHeight > 0 && (
                                <div style={{ height: `${Math.max(containerHeight - 140, 0)}px` }} aria-hidden="true" />
                            )}
                            <div ref={messagesEndRef} className="h-1 sm:h-2" />
                        </div>

                        {/* Fog veil */}
                        <div className="absolute bottom-0 left-0 right-4 h-10 sm:h-14 bg-gradient-to-t from-[#fbfcff] via-[#fbfcff]/80 to-transparent pointer-events-none z-10" />

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

                    {/* ── DOCKED INPUT AREA ── */}
                    <div className="w-full px-2 sm:px-6 pb-1.5 pt-0.5 bg-[#fbfcff] z-20 flex flex-col justify-end border-t border-gray-50/50">
                        <form onSubmit={handleSend} className="relative flex items-end w-full pointer-events-auto group">

                            <div className="relative flex items-end w-full bg-[#fbfcff]/95 backdrop-blur-3xl rounded-3xl border border-gray-200/80 shadow-[0_12px_40px_-10px_rgba(0,0,0,0.1),inset_0_2px_4px_rgba(255,255,255,1)] p-1.5 focus-within:shadow-[0_12px_40px_-10px_rgba(0,0,0,0.15)] focus-within:ring-2 focus-within:ring-gray-200/50 transition-shadow">

                                <textarea
                                    ref={inputRef}
                                    rows={1}
                                    value={input}
                                    onChange={handleInputChange}
                                    onKeyDown={handleKeyDown}
                                    placeholder={`${agentConfig.title} anything...`}
                                    className="flex-1 bg-transparent text-gray-800 placeholder:text-gray-400 text-[0.9375rem] pl-3 pr-2 py-1.5 outline-none resize-none leading-relaxed max-h-[150px] overflow-y-auto chat-scrollbar"
                                />

                                <button
                                    type={isLoading ? "button" : "submit"}
                                    onClick={isLoading ? handleStop : undefined}
                                    disabled={!isLoading && (!input.trim() || !threadId || isLoadingHistory)}
                                    title={isLoading ? "Stop generating" : "Send"}
                                    className={`relative p-1.5 rounded-full transition-all duration-300 flex items-center justify-center shrink-0 ml-1.5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.4)]
                                        ${isLoading
                                            ? 'bg-gray-800 text-white hover:bg-gray-900 shadow-md'
                                            : input.trim()
                                                ? `bg-gradient-to-tr ${agentConfig.color} text-white shadow-md hover:shadow-lg hover:scale-105`
                                                : 'bg-black/5 text-gray-400 hover:text-gray-600 hover:bg-black/10'
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
                            </div>
                        </form>

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
