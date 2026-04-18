import React, { useState, useRef, useEffect } from 'react';
import { useMsal } from "@azure/msal-react";
import { motion } from 'framer-motion';
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

// Utility function to append incoming text chunks to the current message text
const appendChunkSmartly = (current, incoming) => {
  if (!incoming) return current;
  if (!current) return incoming;

  const prev = current[current.length - 1];
  const next = incoming[0];

  const shouldInsertSpace =
    !/\s/.test(prev) &&
    !/\s/.test(next) &&
    (
      (/[A-Za-z0-9]/.test(prev) && /[A-Za-z0-9]/.test(next)) ||
      (/[.!?,:;)\]-]/.test(prev) && /[A-Za-z0-9(]/.test(next))
    );

  return shouldInsertSpace ? `${current} ${incoming}` : current + incoming;
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
        <div className="flex items-center gap-2 mt-2 -mb-1">
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
        // Without this, the OLD agent's threadId stays in state until the async
        // work below finishes, which can cause cross-contamination if the user
        // sends a message during the transition.
        setThreadId('');          // Guard: handleSend checks for empty threadId
        setMessages([]);          // Clear previous agent's messages
        setFeedbackMap({});       // Clear previous agent's feedback
        setIsLoadingHistory(true); // Show spinner during transition

        const loadAgentState = async () => {
            const storageKey = `thread_${agentConfig.id}`;
            const storedThreadId = sessionStorage.getItem(storageKey);
            const isExistingSession = !!storedThreadId;

            // Resolve thread ID: reuse existing or generate new
            const currentThreadId = storedThreadId || uuidv4();
            if (!isExistingSession) {
                sessionStorage.setItem(storageKey, currentThreadId);
            }

            // Set the correct threadId for this agent
            setThreadId(currentThreadId);

            if (isExistingSession) {
                // Existing session -> Fetch History from backend
                try {
                    const response = await fetch(`${API_URL}/api/v1/chat/${agentConfig.id}/${currentThreadId}`);
                    if (!response.ok) throw new Error("Failed to fetch history");

                    const data = await response.json();
                    if (data.messages && data.messages.length > 0) {
                        const mappedMessages = data.messages.map(msg => {
                            let text = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
                            // Re-apply form detection logic for history
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

                        // Load existing feedback for this thread
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
                        // Existing thread but empty history (rare)
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
                // New session -> Default greeting
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
    const messagesEndRef = useRef(null);

    // Auto-scroll to bottom
    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    // Handle Sending Message
    const handleSend = async (e) => {
        e.preventDefault();
        if (!input.trim() || !threadId || isLoadingHistory) return;

        // 1. Add User Message to UI
        const userMessage = { type: 'user', text: input };
        setMessages(prev => [...prev, userMessage]);
        setInput("");
        setIsLoading(true);

        try {
            // 2. Send to Real FastAPI Backend with Streaming
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

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            // --- STREAMING LOGIC ---
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulatedText = "";
            let formType = null;

            // Add an initial empty bot message
            setMessages(prev => [...prev, { type: 'bot', text: "", formType: null }]);
            // setIsLoading(false); // REMOVED: Keep loading dots until we have content

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, { stream: true }).replace(/\r/g, '');
                accumulatedText = appendChunkSmartly(accumulatedText, chunk);

                // Detect and strip Generative UI trigger tokens
                let currentFormType = null;
                let cleanText = accumulatedText;
                for (const [token, type] of Object.entries(FORM_TOKENS)) {
                    if (cleanText.includes(token)) {
                        currentFormType = type;
                        cleanText = cleanText.replace(token, '').trim();
                        break;
                    }
                }

                // Update the last message (the bot message) with the new text
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
            /* Chatbox Width adjustment */
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
                <p className="text-white/70 text-base sm:text-lg max-w-4xl mx-auto font-light">{agentConfig.subtitle}</p>
            </motion.div>

            {/* ── Premium Chat Workspace ─────────────────────── */}
            <motion.div
                initial={{ opacity: 0, y: 25, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: 0.3 }}
                className="relative flex-1 mb-4 sm:mb-8 min-h-0 rounded-2xl sm:rounded-3xl z-10"
            >
                {/* ── LIQUID GLASS AMBIENT AURA ── (Neon glow casting outward from behind the white interface) */}
                <div className={`absolute -inset-2 blur-[30px] opacity-30 bg-gradient-to-br ${agentConfig.color} rounded-[2.5rem] -z-10 transition-colors duration-700 pointer-events-none`} />

                {/* ── SOLID WHITE GLASS WINDOW ── (Highly polished professional inner reading area) */}
                <div className="relative bg-[#fbfcff] w-full h-full rounded-2xl sm:rounded-3xl border border-white/80 shadow-[0_20px_50px_-10px_rgba(0,0,0,0.2),inset_0_1px_1px_rgba(255,255,255,1)] flex flex-col overflow-hidden">

                    {/* Messages Area Wrapper */}
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
                                        {/* Chatbubble width adjustment */}
                                        <div className={`max-w-[80%] sm:max-w-[75%] rounded-2xl px-5 sm:px-6 py-3.5 sm:py-4 text-[0.9375rem] leading-relaxed shadow-sm ${msg.type === 'user'
                                            ? `bg-gradient-to-br ${agentConfig.color} text-white rounded-tr-md`
                                            : 'bg-white/95 border border-gray-100/60 text-gray-700 rounded-tl-md'
                                            }`}>
                                            <div className="prose prose-sm max-w-none text-inherit">
                                                {(() => {
                                                    // Logic to separate text from sources
                                                    const parts = msg.text.split(/(Sources:)/);
                                                    const mainText = parts[0];
                                                    const sourcesPart = parts.length > 2 ? parts.slice(2).join("") : "";
                                                    
                                                    // Parse sources list: "[Name](URL), [Name](URL)"
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
                                                                {mainText}
                                                            </ReactMarkdown>
                                                            {msg.type === 'bot' && (
                                                                <SourcesSection sources={sources} color={agentConfig.color} />
                                                            )}
                                                        </>
                                                    );
                                                })()}
                                            </div>
                                            {/* Render Generative UI form if triggered */}
                                            {msg.formType === 'lifestore' && <LifestoreForm />}
                                            {msg.formType === 'enterprise' && <EnterpriseForm />}

                                            {/* Feedback buttons for bot messages (not for greeting/first message) */}
                                            {msg.type === 'bot' && index > 0 && msg.text && !isLoading && (
                                                <FeedbackButtons
                                                    messageIndex={index}
                                                    agentId={agentConfig.id}
                                                    threadId={threadId}
                                                    userId={user.username || "anonymous"}
                                                    existingRating={feedbackMap[index] || null}
                                                    onFeedback={(idx, rating) => setFeedbackMap(prev => ({ ...prev, [idx]: rating }))}
                                                />
                                            )}
                                        </div>
                                    </motion.div>
                                )
                            ))}
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
                        
                        {/* ── FIXED FOG VEIL (Fixed to bottom of window, clears scrollbar) ── */}
                        <div className="absolute bottom-0 left-0 right-4 h-10 sm:h-14 bg-gradient-to-t from-[#fbfcff] via-[#fbfcff]/80 to-transparent pointer-events-none z-10" />
                    </div>

                    {/* ── DOCKED INPUT AREA ── */}
                    <div className="w-full px-2 sm:px-6 pb-1.5 pt-0.5 bg-[#fbfcff] z-20 flex flex-col justify-end border-t border-gray-50/50">
                        <form onSubmit={handleSend} className="relative flex items-center w-full pointer-events-auto group">

                            {/* Inner Solid Pill Component (Grey Border / Full Width) */}
                            <div className="relative flex items-center w-full bg-[#fbfcff]/95 backdrop-blur-3xl rounded-full border border-gray-200/80 shadow-[0_12px_40px_-10px_rgba(0,0,0,0.1),inset_0_2px_4px_rgba(255,255,255,1)] p-0.5 focus-within:shadow-[0_12px_40px_-10px_rgba(0,0,0,0.15)] focus-within:ring-2 focus-within:ring-gray-200/50 transition-shadow">

                                <input
                                    type="text"
                                    value={input}
                                    onChange={(e) => setInput(e.target.value)}
                                    placeholder={`${agentConfig.title} anything...`}
                                    className="flex-1 bg-transparent text-gray-800 placeholder:text-gray-400 text-[0.9375rem] pl-4 py-1.5 sm:py-[0.375rem] outline-none"
                                />

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