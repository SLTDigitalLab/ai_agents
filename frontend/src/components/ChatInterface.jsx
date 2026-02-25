import React, { useState, useRef, useEffect } from 'react';
import { useMsal } from "@azure/msal-react";
import { motion } from 'framer-motion';
import { v4 as uuidv4 } from 'uuid';
import LifestoreForm from './forms/LifestoreForm';
import EnterpriseForm from './forms/EnterpriseForm';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Generative UI trigger tokens emitted by the backend
const FORM_TOKENS = {
    '[RENDER_LIFESTORE_FORM]': 'lifestore',
    '[RENDER_ENTERPRISE_FORM]': 'enterprise',
};



const ChatInterface = ({ agentConfig }) => {
    const { accounts } = useMsal();
    const user = accounts[0] || { name: "User" };

    // State for thread ID and messages
    const [threadId, setThreadId] = useState('');
    const [messages, setMessages] = useState([]);
    const [isLoadingHistory, setIsLoadingHistory] = useState(false);

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
                    const response = await fetch(`http://localhost:8000/api/v1/chat/${agentConfig.id}/${currentThreadId}`);
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
            // 2. Send to Real FastAPI Backend
            const response = await fetch('http://localhost:8000/api/v1/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: userMessage.text,
                    agent_id: agentConfig.id, // e.g., "finance"
                    user_id: user.username || "anonymous",
                    thread_id: threadId
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            // 3. Add AI Response to UI
            // Ensure the response is a string to prevent React rendering crashes
            let safeText = data.response;
            if (typeof safeText !== 'string') {
                console.warn("Received non-string response from backend:", safeText);
                safeText = JSON.stringify(safeText);
            }

            // Detect and strip Generative UI trigger tokens
            let formType = null;
            for (const [token, type] of Object.entries(FORM_TOKENS)) {
                if (safeText.includes(token)) {
                    formType = type;
                    safeText = safeText.replace(token, '').trim();
                    break;
                }
            }

            const botMessage = {
                type: 'bot',
                text: safeText,
                formType: formType,
            };
            setMessages(prev => [...prev, botMessage]);

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
            className="flex-1 flex flex-col w-full max-w-6xl mx-auto px-4 z-10 pt-6 pb-0 min-h-0 overflow-hidden"
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
                className="relative h-[63vh] min-h-0 rounded-2xl sm:rounded-3xl overflow-hidden"
                style={{
                    boxShadow: `0 0 0 1px rgba(255,255,255,0.06), 0 0 40px -10px rgba(255,255,255,0.05), inset 0 1px 0 rgba(255,255,255,0.04)`
                }}
            >
                {/* Inset glass container */}
                <div className="absolute inset-0 bg-white/[0.03] backdrop-blur-sm rounded-2xl sm:rounded-3xl pointer-events-none" />

                <div className="relative bg-white w-full h-full rounded-2xl sm:rounded-3xl shadow-2xl flex flex-col overflow-hidden">

                    {/* Messages Area */}
                    <div className="flex-1 overflow-y-auto p-6 sm:p-8 space-y-5 chat-scrollbar relative">
                        {isLoadingHistory && (
                            <div className="absolute inset-0 bg-white/50 backdrop-blur-sm z-10 flex items-center justify-center">
                                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900"></div>
                            </div>
                        )}
                        {messages.map((msg, index) => (
                            <motion.div
                                key={index}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ duration: 0.35, ease: 'easeOut' }}
                                className={`flex ${msg.type === 'user' ? 'justify-end' : 'justify-start'}`}
                            >
                                <div className={`max-w-[75%] sm:max-w-[70%] rounded-2xl px-5 sm:px-6 py-3.5 sm:py-4 text-[0.9375rem] leading-relaxed ${msg.type === 'user'
                                    ? `bg-gradient-to-br ${agentConfig.color} text-white rounded-tr-md shadow-lg`
                                    : 'bg-gray-50 border border-gray-100 text-gray-700 rounded-tl-md shadow-sm'
                                    }`}>
                                    <div className="prose prose-sm max-w-none text-inherit dark:prose-invert">
                                        <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                            components={{
                                                p: ({ node, ...props }) => <p className="mb-2 last:mb-0" {...props} />,
                                                a: ({ node, ...props }) => <a className="text-blue-500 hover:underline" target="_blank" rel="noopener noreferrer" {...props} />,
                                                ul: ({ node, ...props }) => <ul className="list-disc pl-4 mb-2 space-y-1" {...props} />,
                                                ol: ({ node, ...props }) => <ol className="list-decimal pl-4 mb-2 space-y-1" {...props} />,
                                                li: ({ node, ...props }) => <li className="pl-1" {...props} />,
                                                table: ({ node, ...props }) => (
                                                    <div className="overflow-x-auto my-4 rounded-lg border border-gray-200">
                                                        <table className="w-full text-sm text-left border-collapse" {...props} />
                                                    </div>
                                                ),
                                                th: ({ node, ...props }) => <th className="bg-purple-50 px-4 py-2 font-semibold border-b border-gray-200" {...props} />,
                                                td: ({ node, ...props }) => <td className="px-4 py-2 border-b border-gray-100" {...props} />,
                                                tr: ({ node, ...props }) => <tr className="even:bg-gray-50 hover:bg-gray-100 transition-colors" {...props} />,
                                                code: ({ node, inline, className, children, ...props }) => {
                                                    return inline ? (
                                                        <code className="bg-gray-100 px-1 py-0.5 rounded text-sm font-mono text-pink-600" {...props}>
                                                            {children}
                                                        </code>
                                                    ) : (
                                                        <code className="block bg-gray-100 p-2 rounded text-sm font-mono overflow-x-auto my-2" {...props}>
                                                            {children}
                                                        </code>
                                                    );
                                                }
                                            }}
                                        >
                                            {msg.text}
                                        </ReactMarkdown>
                                    </div>
                                    {/* Render Generative UI form if triggered */}
                                    {msg.formType === 'lifestore' && <LifestoreForm />}
                                    {msg.formType === 'enterprise' && <EnterpriseForm />}
                                </div>
                            </motion.div>
                        ))}
                        {isLoading && (
                            <div className="flex justify-start">
                                <div className="bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-md px-6 py-4 shadow-sm flex gap-1.5 items-center">
                                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce" />
                                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce [animation-delay:150ms]" />
                                    <div className="w-2 h-2 rounded-full bg-gray-300 animate-bounce [animation-delay:300ms]" />
                                </div>
                            </div>
                        )}
                        <div ref={messagesEndRef} />
                    </div>

                    {/* Input Area */}
                    <div className="p-4 sm:p-6 bg-white border-t border-gray-100/80">
                        <form onSubmit={handleSend} className="relative flex items-center">
                            <input
                                type="text"
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                placeholder="Type a message..."
                                className="w-full bg-gray-50/80 text-gray-800 rounded-2xl pl-5 sm:pl-6 pr-14 py-3.5 sm:py-4 focus:outline-none focus:ring-2 focus:ring-gray-200/80 transition-all border border-gray-200/70 placeholder:text-gray-400 text-[0.9375rem]"
                            />
                            <button
                                type="submit"
                                disabled={!input.trim() || isLoading || !threadId || isLoadingHistory}
                                className="absolute right-2 p-2.5 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 sm:w-6 sm:h-6">
                                    <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                                </svg>
                            </button>
                        </form>
                        <p className="text-center text-xs text-gray-400/80 mt-3 font-light">
                            {agentConfig.disclaimer}
                        </p>
                    </div>
                </div>
            </motion.div>
        </motion.div>
    );
};

export default ChatInterface;