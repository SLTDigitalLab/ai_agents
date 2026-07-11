import React, { useState, useRef, useEffect, useLayoutEffect, useMemo, forwardRef, useImperativeHandle } from 'react';
import { useMsal } from "@azure/msal-react";
import { motion, AnimatePresence } from 'framer-motion';
import { v4 as uuidv4 } from 'uuid';
import LifestoreForm from './forms/LifestoreForm';
import EnterpriseForm from './forms/EnterpriseForm';
import embryoLogo from '../assets/embryo-removebg.png';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// All agents now use the normal LangGraph chat route.
// LifeStore MCP tools are selected inside the backend LangGraph agent,
// not forced from the frontend.
const getChatEndpoint = () => `${API_URL}/api/v1/chat`;

const HISTORY_TIMEOUT_MS = 8000;

// Hidden backend metadata block used only by the frontend.
// The backend can append this at the end of a streamed answer; the UI removes
// it from visible text and renders proper product image cards instead.
const PRODUCT_CARDS_START = '[LIFESTORE_PRODUCT_CARDS]';
const PRODUCT_CARDS_END = '[/LIFESTORE_PRODUCT_CARDS]';
const PRODUCT_CARD_MAX_ITEMS = 24;

const getHistoryEndpoint = (agentId, threadId) => (
    `${API_URL}/api/v1/chat/${agentId}/${threadId}`
);

const fetchWithTimeout = async (url, options = {}, timeoutMs = HISTORY_TIMEOUT_MS) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
        return await fetch(url, {
            ...options,
            signal: options.signal || controller.signal,
        });
    } finally {
        clearTimeout(timeoutId);
    }
};

const localMessagesKey = (agentId, threadId) => `chat_messages_${agentId}_${threadId}`;

const safeJsonParse = (value, fallback = null) => {
    try {
        return JSON.parse(value);
    } catch {
        return fallback;
    }
};

const loadLocalMessages = (agentId, threadId) => {
    const raw = localStorage.getItem(localMessagesKey(agentId, threadId));
    const parsed = safeJsonParse(raw, []);
    return Array.isArray(parsed) ? parsed : [];
};

const saveLocalMessages = (agentId, threadId, messages) => {
    if (!agentId || !threadId || !Array.isArray(messages)) return;

    const cleanMessages = messages
        .filter((msg) => msg && (msg.type === 'user' || msg.text || msg.formType || (msg.productCards && msg.productCards.length)))
        .map((msg) => ({
            type: msg.type,
            text: msg.text || '',
            formType: msg.formType || null,
            formData: msg.formData || null,
            productCards: Array.isArray(msg.productCards) ? msg.productCards : [],
            productCardDisplay: msg.productCardDisplay || null,
            error: !!msg.error,
            timestamp: msg.timestamp || Date.now(),
        }));

    localStorage.setItem(localMessagesKey(agentId, threadId), JSON.stringify(cleanMessages));
};

const localFeedbackKey = (agentId, threadId) => `chat_feedback_${agentId}_${threadId}`;

const loadLocalFeedback = (agentId, threadId) => {
    const raw = localStorage.getItem(localFeedbackKey(agentId, threadId));
    const parsed = safeJsonParse(raw, {});
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
};

const saveLocalFeedback = (agentId, threadId, feedbackMap) => {
    if (!agentId || !threadId || !feedbackMap || typeof feedbackMap !== 'object') return;
    localStorage.setItem(localFeedbackKey(agentId, threadId), JSON.stringify(feedbackMap));
};

const isBadLifeStoreImageUrl = (url) => {
    const value = String(url || '').toLowerCase();

    if (!value) return true;

    const badHints = [
        '970_90',
        'inline-images/970_90',
        'eteleshop',
        'teleshop',
        '/themes/shop/images/',
        'union-pay',
        'visa',
        'master',
        'american',
        'payment',
        'logo',
        'sltmobitel',
        'chat',
        'footer',
        'header',
        'banner',
        'sprite',
        'loader',
        'ajax-loader',
        'placeholder',
        'default-image',
        'no-image',
    ];

    if (badHints.some((hint) => value.includes(hint))) return true;
    if (value.startsWith('data:')) return true;
    if (value.endsWith('.svg')) return true;

    return false;
};

const isLikelyImageUrl = (url) => {
    const value = String(url || '').toLowerCase();

    if (!value.startsWith('http://') && !value.startsWith('https://')) return false;
    if (isBadLifeStoreImageUrl(value)) return false;

    return (
        value.includes('/sites/default/files/') ||
        value.match(/\.(jpg|jpeg|png|webp|gif)(\?|$)/)
    );
};

const cleanProductText = (value) => {
    if (value === null || value === undefined) return '';
    return String(value).replace(/\s+/g, ' ').trim();
};

const normalizeProductCard = (raw) => {
    if (!raw || typeof raw !== 'object') return null;

    const name = cleanProductText(
        raw.name ||
        raw.title ||
        raw.product_name ||
        raw.productName ||
        raw.product ||
        raw.item_name
    );

    const url = cleanProductText(
        raw.url ||
        raw.product_url ||
        raw.productUrl ||
        raw.link ||
        raw.source_url
    );

    const imageUrl = cleanProductText(
        raw.image_url ||
        raw.imageUrl ||
        raw.image ||
        raw.thumbnail ||
        raw.thumbnail_url ||
        raw.photo
    );

    if (!name && !url && !imageUrl) return null;

    return {
        id: cleanProductText(raw.product_id || raw.id || raw.sku || url || name),
        name: name || 'LifeStore product',
        url,
        image_url: isLikelyImageUrl(imageUrl) ? imageUrl : '',
        price: cleanProductText(raw.price || raw.unit_price || raw.price_text),
        price_value: raw.price_value ?? raw.unit_price_value ?? null,
        currency: cleanProductText(raw.currency || 'LKR'),
        stock_status: cleanProductText(raw.stock_status || raw.availability || raw.status),
        category: cleanProductText(raw.category || raw.category_name),
        product_type: cleanProductText(raw.product_type || raw.productType || raw.type),
        brand: cleanProductText(raw.brand),
        seller: cleanProductText(raw.seller || raw.sold_by),
        description: cleanProductText(raw.short_description || raw.summary || raw.description),
        key_details: Array.isArray(raw.key_details)
            ? raw.key_details.map(cleanProductText).filter(Boolean).slice(0, 6)
            : [],
    };
};

const collectProductObjects = (value, output = [], depth = 0) => {
    if (!value || depth > 5) return output;

    if (Array.isArray(value)) {
        value.forEach((item) => collectProductObjects(item, output, depth + 1));
        return output;
    }

    if (typeof value !== 'object') return output;

    const hasProductShape =
        value.image_url ||
        value.imageUrl ||
        value.product_url ||
        value.productUrl ||
        value.price ||
        value.price_value ||
        value.stock_status ||
        value.availability ||
        value.category ||
        value.brand;

    const maybeCard = normalizeProductCard(value);
    if (hasProductShape && maybeCard) {
        output.push(maybeCard);
    }

    const likelyContainers = [
        'products',
        'items',
        'results',
        'matches',
        'recommendations',
        'data',
        'product',
        'tool_result',
        'tool_results',
        'metadata',
    ];

    for (const key of likelyContainers) {
        if (Object.prototype.hasOwnProperty.call(value, key)) {
            collectProductObjects(value[key], output, depth + 1);
        }
    }

    return output;
};

const dedupeProductCards = (cards) => {
    const seen = new Set();
    const deduped = [];

    for (const card of cards) {
        if (!card) continue;
        const key = card.url || card.id || card.name;
        if (!key || seen.has(key)) continue;

        seen.add(key);
        deduped.push(card);
    }

    return deduped;
};

const hasRenderableProductImage = (product) => {
    return !!product && isLikelyImageUrl(product.image_url);
};

const prepareProductCardsForDisplay = (cards, maxItems = PRODUCT_CARD_MAX_ITEMS) => {
    const values = Array.isArray(cards) ? cards : [];
    return dedupeProductCards(values)
        .filter(hasRenderableProductImage)
        .slice(0, maxItems);
};

const normalizeProductDisplay = (display, productCount = 0) => {
    const value = String(display || '').toLowerCase().trim();

    if (['comparison', 'compare', 'comparison_grid', 'comparison-image-grid', 'comparison_image_grid'].includes(value)) {
        return 'comparison';
    }

    if (['carousel', 'slideshow', 'slider', 'multi', 'multiple'].includes(value)) {
        return 'carousel';
    }

    if (['single', 'card', 'product'].includes(value)) {
        return 'single';
    }

    if (productCount > 1) return 'carousel';
    if (productCount === 1) return 'single';
    return null;
};

const mergeProductDisplay = (...displays) => {
    const normalized = displays
        .map((display) => normalizeProductDisplay(display))
        .filter(Boolean);

    if (normalized.includes('comparison')) return 'comparison';
    if (normalized.includes('carousel')) return 'carousel';
    if (normalized.includes('single')) return 'single';
    return null;
};

const inferProductDisplayFromJsonResponse = (data, cardCount = 0) => {
    if (!data || typeof data !== 'object') {
        return normalizeProductDisplay(null, cardCount);
    }

    const frontendContract = data.frontend_contract || data.frontendContract || {};
    const renderAs = String(frontendContract.render_as || data.render_as || data.renderAs || '').toLowerCase();
    const display = data.display || data.card_display || data.productCardDisplay || frontendContract.display;

    if (renderAs.includes('comparison')) {
        return 'comparison';
    }

    return normalizeProductDisplay(display, cardCount);
};

const extractProductCardPayloadFromJsonResponse = (data) => {
    if (!data || typeof data !== 'object') {
        return { cards: [], display: null };
    }

    const explicitProducts = data.product_cards || data.cards || data.products;
    let cards = [];

    if (explicitProducts) {
        const values = Array.isArray(explicitProducts) ? explicitProducts : [explicitProducts];
        cards = prepareProductCardsForDisplay(values.map(normalizeProductCard).filter(Boolean));
    } else {
        cards = prepareProductCardsForDisplay(collectProductObjects(data));
    }

    return {
        cards,
        display: inferProductDisplayFromJsonResponse(data, cards.length),
    };
};

const extractProductCardsFromJsonResponse = (data) => {
    return extractProductCardPayloadFromJsonResponse(data).cards;
};

const removeBadImageUrlLines = (text) => {
    if (!text) return '';

    return String(text)
        .split('\n')
        .filter((line) => {
            const low = line.toLowerCase();
            const hasDirectImageUrl = /https?:\/\/\S+\.(jpg|jpeg|png|webp|gif)(\?\S*)?/i.test(line);
            const looksLikeImageField = /(^|\b)(image[_\s-]*url|image url|thumbnail[_\s-]*url)(\b|\s*:)/i.test(line);

            // Keep normal product links, but remove raw image URL lines.
            if (looksLikeImageField) return false;
            if (hasDirectImageUrl && low.includes('image')) return false;
            if (isBadLifeStoreImageUrl(low)) return false;

            return true;
        })
        .join('\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
};


const extractEmbeddedProductCardsFromText = (text) => {
    const sourceText = String(text || '');
    let cleanText = sourceText;
    const extractedCards = [];
    let extractedDisplay = null;

    while (true) {
        const startIdx = cleanText.indexOf(PRODUCT_CARDS_START);
        if (startIdx === -1) break;

        const payloadStart = startIdx + PRODUCT_CARDS_START.length;
        const endIdx = cleanText.indexOf(PRODUCT_CARDS_END, payloadStart);

        // Hide an incomplete metadata block while the stream is still arriving.
        if (endIdx === -1) {
            cleanText = cleanText.slice(0, startIdx).trimEnd();
            break;
        }

        const rawPayload = cleanText.slice(payloadStart, endIdx).trim();
        const before = cleanText.slice(0, startIdx);
        const after = cleanText.slice(endIdx + PRODUCT_CARDS_END.length);

        try {
            const parsed = JSON.parse(rawPayload);
            const payload = extractProductCardPayloadFromJsonResponse(parsed);
            extractedCards.push(...payload.cards);
            extractedDisplay = mergeProductDisplay(extractedDisplay, payload.display);
        } catch (error) {
            console.warn('Failed to parse LifeStore product cards payload:', error);
        }

        cleanText = `${before}${after}`;
    }

    const productCards = prepareProductCardsForDisplay(extractedCards);

    return {
        text: removeBadImageUrlLines(cleanText).trim(),
        productCards,
        productCardDisplay: mergeProductDisplay(extractedDisplay, normalizeProductDisplay(null, productCards.length)),
    };
};

const tryParseJsonPayload = (text) => {
    if (!text || typeof text !== 'string') return null;

    const trimmed = text.trim();
    if (!trimmed) return null;

    try {
        return JSON.parse(trimmed);
    } catch {
        // Continue below.
    }

    // Supports simple SSE-style streams: data: { ... }
    const dataLines = trimmed
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trim())
        .filter((line) => line && line !== '[DONE]');

    for (let i = dataLines.length - 1; i >= 0; i -= 1) {
        try {
            return JSON.parse(dataLines[i]);
        } catch {
            // Keep checking earlier lines.
        }
    }

    return null;
};

const extractTextFromJsonResponse = (data) => {
    if (!data) return '';

    let text =
        data.reply ||
        data.response ||
        data.answer ||
        data.message ||
        data.content ||
        data.text ||
        '';

    if (typeof text !== 'string') {
        text = JSON.stringify(text, null, 2);
    }

    text = removeBadImageUrlLines(text);

    const sources = data.sources || data.source_documents || data.references || [];
    if (Array.isArray(sources) && sources.length > 0) {
        const renderedSources = sources
            .map((src, index) => {
                const name = src.name || src.title || src.source || src.url || `Source ${index + 1}`;
                const url = src.url || src.link || src.source_url;
                return url ? `[${name}](${url})` : null;
            })
            .filter(Boolean);

        if (renderedSources.length > 0) {
            text += `\n\nSources:\n${renderedSources.join('\n')}`;
        }
    }

    return text;
};

const buildBotMessageFromJsonResponse = (data) => {
    const directPayload = extractProductCardPayloadFromJsonResponse(data);
    let text = extractTextFromJsonResponse(data);
    const embedded = extractEmbeddedProductCardsFromText(text);
    text = embedded.text;

    let formType = null;

    for (const [token, type] of Object.entries(FORM_TOKENS)) {
        if (text.includes(token)) {
            formType = type;
            text = text.replace(token, '').trim();
            break;
        }
    }

    const productCards = prepareProductCardsForDisplay([...directPayload.cards, ...embedded.productCards]);

    return {
        type: 'bot',
        text,
        productCards,
        productCardDisplay: mergeProductDisplay(
            directPayload.display,
            embedded.productCardDisplay,
            normalizeProductDisplay(null, productCards.length),
        ),
        formType,
        formData: data.form_payload || data.formData || null,
        timestamp: Date.now(),
    };
};

const mapHistoryMessage = (msg) => {
    let text = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content || '');
    let formType = null;

    for (const [token, type] of Object.entries(FORM_TOKENS)) {
        if (text.includes(token)) {
            formType = type;
            text = text.replace(token, '').trim();
            break;
        }
    }

    const embedded = extractEmbeddedProductCardsFromText(text);
    text = embedded.text;

    const directPayload = extractProductCardPayloadFromJsonResponse(msg);
    const productCards = prepareProductCardsForDisplay([
        ...directPayload.cards,
        ...embedded.productCards,
    ]);

    return {
        type: msg.type === 'human' || msg.role === 'user' ? 'user' : 'bot',
        text: removeBadImageUrlLines(text),
        formType,
        formData: msg.formData || msg.form_payload || null,
        productCards,
        productCardDisplay: mergeProductDisplay(
            msg.productCardDisplay,
            directPayload.display,
            embedded.productCardDisplay,
            normalizeProductDisplay(null, productCards.length),
        ),
        timestamp: msg.timestamp || Date.now(),
    };
};



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

const normalizeAssistantMarkdown = (text) => {
    if (!text) return text;

    let output = String(text)
        .replace(/\r/g, '')
        .replace(/\s*\|\|\s*/g, '\n')
        .replace(/Products found:\s*\*\*(\d+)\*\*\s*Products/gi, 'Products found: **$1**')
        .replace(/Products found:\s*(\d+)\s*Products/gi, 'Products found: **$1**')
        .replace(/(Products found:\s*\*\*\d+\*\*)\s*\|\s*Product\s*\|/gi, '$1\n\n**Products**\n\n| Product |')
        .replace(/(Products found:\s*\d+)\s*\|\s*Product\s*\|/gi, '$1\n\n**Products**\n\n| Product |')
        .replace(/\s+(\*\*(Overview|Products|Important notes|Key details|Best for|Summary|Key differences)\*\*)/g, '\n\n$1\n')
        .replace(/(\|\s*Product\s*\|[^\n]*)\s+(\|\s*-{3,})/g, '$1\n$2')
        .replace(/(\|\s*-{3,}[^\n]*)\s+(\|\s*\*\*)/g, '$1\n$2')
        .replace(/\n{3,}/g, '\n\n');

    // If the model accidentally streams a table row on the same line as prose,
    // make ReactMarkdown/GFM see it as a real table instead of paragraph text.
    output = output.replace(/([^\n])\s+(\|\s*Product\s*\|)/g, '$1\n\n$2');
    output = output.replace(/([^\n])\s+(\|\s*Feature\s*\|)/g, '$1\n\n$2');

    return output.trim();
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


const ProductImage = ({ src, name }) => {
    const [failed, setFailed] = useState(false);

    if (!src || failed || !isLikelyImageUrl(src)) {
        return null;
    }

    return (
        <img
            src={src}
            alt={name || "LifeStore product"}
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setFailed(true)}
            className="w-full h-full object-contain bg-white"
        />
    );
};

const ProductCard = ({ product, color, compact = false }) => {
    const availability = product.stock_status || '';
    const isInStock = availability.toLowerCase().includes('in_stock') || availability.toLowerCase().includes('in stock');

    return (
        <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            className="overflow-hidden rounded-3xl border border-gray-200/80 dark:border-gray-700/80 bg-white/95 dark:bg-gray-900/80 shadow-[0_18px_60px_-28px_rgba(0,0,0,0.35)]"
        >
            <div className={`${compact ? 'h-48' : 'h-56'} w-full bg-white dark:bg-gray-950 border-b border-gray-100 dark:border-gray-800`}>
                <ProductImage src={product.image_url} name={product.name} />
            </div>

            <div className="p-4 sm:p-5">
                <h3 className="text-sm sm:text-base font-semibold text-gray-900 dark:text-gray-100 leading-snug">
                    {product.name}
                </h3>

                <div className="mt-3 flex flex-wrap gap-1.5">
                    {product.price && (
                        <span className="inline-flex items-center rounded-full bg-gray-100 dark:bg-gray-800 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200">
                            {product.price}
                        </span>
                    )}

                    {availability && (
                        <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${isInStock
                                ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                                : 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300'
                            }`}>
                            {availability.replaceAll('_', ' ')}
                        </span>
                    )}
                </div>

                <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
                    {product.brand && <p><span className="font-medium text-gray-600 dark:text-gray-300">Brand:</span> {product.brand}</p>}
                    {product.category && <p><span className="font-medium text-gray-600 dark:text-gray-300">Category:</span> {product.category}</p>}
                    {product.product_type && <p><span className="font-medium text-gray-600 dark:text-gray-300">Product type:</span> {product.product_type}</p>}
                    {product.seller && <p><span className="font-medium text-gray-600 dark:text-gray-300">Seller:</span> {product.seller}</p>}
                </div>

                {Array.isArray(product.key_details) && product.key_details.length > 0 && (
                    <ul className="mt-3 list-disc pl-4 text-xs leading-relaxed text-gray-500 dark:text-gray-400 space-y-1">
                        {product.key_details.slice(0, 3).map((detail, detailIndex) => (
                            <li key={detailIndex}>{detail}</li>
                        ))}
                    </ul>
                )}

                {product.description && (!product.key_details || product.key_details.length === 0) && (
                    <p className="mt-3 text-xs leading-relaxed text-gray-500 dark:text-gray-400 line-clamp-3">
                        {product.description}
                    </p>
                )}

                {product.url && (
                    <a
                        href={product.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={`mt-4 inline-flex items-center justify-center rounded-full bg-gradient-to-tr ${color} px-4 py-2 text-xs font-semibold text-white shadow-sm hover:opacity-95`}
                    >
                        View product
                    </a>
                )}
            </div>
        </motion.div>
    );
};

const ComparisonProductCards = ({ products, color }) => {
    const safeProducts = useMemo(() => prepareProductCardsForDisplay(products, 6), [products]);

    if (safeProducts.length === 0) return null;

    return (
        <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            className="mt-5 mb-1"
        >
            <div className="mb-3">
                <p className="text-[0.7rem] uppercase tracking-[0.18em] font-bold text-gray-400 dark:text-gray-500">
                    Visual comparison
                </p>
                <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">
                    Compared LifeStore products with images
                </h3>
            </div>

            <div className={`grid grid-cols-1 ${safeProducts.length === 2 ? 'md:grid-cols-2' : 'md:grid-cols-2 xl:grid-cols-3'} gap-4`}>
                {safeProducts.map((product, index) => (
                    <div key={product.id || product.url || product.name || index} className="relative">
                        <div className={`absolute -top-2 left-4 z-10 rounded-full bg-gradient-to-tr ${color} px-3 py-1 text-[0.65rem] font-bold uppercase tracking-wide text-white shadow-sm`}>
                            Product {index + 1}
                        </div>
                        <ProductCard product={product} color={color} compact />
                    </div>
                ))}
            </div>
        </motion.section>
    );
};

const ProductCards = ({ products, color, display = null }) => {
    const safeProducts = useMemo(() => prepareProductCardsForDisplay(products), [products]);
    const [activeIndex, setActiveIndex] = useState(0);

    useEffect(() => {
        setActiveIndex((prev) => {
            if (safeProducts.length === 0) return 0;
            return Math.min(prev, safeProducts.length - 1);
        });
    }, [safeProducts.length]);

    if (safeProducts.length === 0) return null;

    const displayMode = normalizeProductDisplay(display, safeProducts.length);

    if (displayMode === 'comparison' && safeProducts.length > 1) {
        return <ComparisonProductCards products={safeProducts} color={color} />;
    }

    const isCarousel = safeProducts.length > 1;
    const activeProduct = safeProducts[Math.min(activeIndex, safeProducts.length - 1)];

    const goPrevious = () => {
        setActiveIndex((prev) => (prev - 1 + safeProducts.length) % safeProducts.length);
    };

    const goNext = () => {
        setActiveIndex((prev) => (prev + 1) % safeProducts.length);
    };

    return (
        <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            className="mt-5 mb-1"
        >
            <div className="flex items-center justify-between gap-3 mb-3">
                <div>
                    <p className="text-[0.7rem] uppercase tracking-[0.18em] font-bold text-gray-400 dark:text-gray-500">
                        {isCarousel ? 'Product slideshow' : 'Product card'}
                    </p>
                    <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">
                        {isCarousel ? 'Matched LifeStore products with images' : 'Matched LifeStore product'}
                    </h3>
                </div>

                {isCarousel && (
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-400 dark:text-gray-500">
                            {activeIndex + 1} / {safeProducts.length}
                        </span>
                        <button
                            type="button"
                            onClick={goPrevious}
                            className="h-8 w-8 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-500 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white shadow-sm"
                            title="Previous product"
                        >
                            ‹
                        </button>
                        <button
                            type="button"
                            onClick={goNext}
                            className={`h-8 w-8 rounded-full bg-gradient-to-tr ${color} text-white shadow-sm hover:opacity-95`}
                            title="Next product"
                        >
                            ›
                        </button>
                    </div>
                )}
            </div>

            <div className="relative overflow-hidden">
                <AnimatePresence mode="wait" initial={false}>
                    <motion.div
                        key={activeProduct.id || activeProduct.url || activeProduct.name || activeIndex}
                        initial={{ opacity: 0, x: 18 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -18 }}
                        transition={{ duration: 0.22, ease: 'easeOut' }}
                    >
                        <ProductCard product={activeProduct} color={color} compact={isCarousel} />
                    </motion.div>
                </AnimatePresence>
            </div>

            {isCarousel && (
                <div className="mt-1.5 flex justify-center gap-1.5 overflow-x-auto px-2 pb-1">
                    {safeProducts.map((product, index) => (
                        <button
                            key={product.id || product.url || index}
                            type="button"
                            onClick={() => setActiveIndex(index)}
                            aria-label={`Show product ${index + 1}`}
                            className={`h-1.5 rounded-full transition-all ${index === activeIndex
                                    ? 'w-6 bg-gray-800 dark:bg-gray-100'
                                    : 'w-1.5 bg-gray-300 dark:bg-gray-700 hover:bg-gray-400 dark:hover:bg-gray-600'
                                }`}
                        />
                    ))}
                </div>
            )}
        </motion.section>
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
    const [error, setError] = useState(false);

    useEffect(() => {
        setRating(existingRating || null);
    }, [existingRating]);

    const persistLocalFeedback = (finalRating) => {
        const current = loadLocalFeedback(agentId, threadId);
        const next = { ...current };

        if (finalRating) {
            next[messageIndex] = finalRating;
        } else {
            delete next[messageIndex];
        }

        saveLocalFeedback(agentId, threadId, next);
    };

    const handleFeedback = async (newRating) => {
        if (submitting) return;

        const finalRating = rating === newRating ? null : newRating;

        // Make the button work immediately even if the optional backend feedback
        // route is not available in this MCP frontend test project.
        setRating(finalRating);
        setError(false);
        persistLocalFeedback(finalRating);
        onFeedback?.(messageIndex, finalRating);

        setSubmitting(true);
        try {
            const res = await fetch(`${API_URL}/api/v1/feedback`, {
                method: finalRating ? 'POST' : 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    agent_id: agentId,
                    thread_id: threadId,
                    message_index: messageIndex,
                    rating: finalRating || newRating,
                    user_id: userId,
                }),
            });

            // Keep the local rating even when this demo backend does not expose
            // /api/v1/feedback. The amber title simply warns that it was not
            // synced to the server.
            if (!res.ok) {
                setError(true);
            }
        } catch (err) {
            console.warn('Feedback saved locally but backend sync failed:', err);
            setError(true);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <>
            <button
                type="button"
                onClick={() => handleFeedback('up')}
                disabled={submitting}
                className={`p-1.5 rounded-md transition-all duration-200 ${rating === 'up'
                    ? 'text-emerald-500 bg-emerald-50 dark:bg-emerald-500/10'
                    : error
                        ? 'text-amber-500 bg-amber-50 dark:bg-amber-500/10'
                        : 'text-gray-300 dark:text-gray-600 hover:text-emerald-400 hover:bg-emerald-50/50 dark:hover:bg-emerald-500/10'
                    }`}
                title={error ? "Saved locally. Backend feedback sync failed." : "Helpful"}
            >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                    <path d="M1 8.998a1 1 0 011-1h.764a1.483 1.483 0 00-.076.506v5.996a1.483 1.483 0 00.076.506H2a1 1 0 01-1-1V8.998zM5.25 7.726a2 2 0 01.944-1.697l3.476-2.14a1.5 1.5 0 012.33 1.25v2.363h2.5a2 2 0 011.96 2.4l-.782 3.908A2 2 0 0113.72 15.5H5.25V7.726z" />
                </svg>
            </button>
            <button
                type="button"
                onClick={() => handleFeedback('down')}
                disabled={submitting}
                className={`p-1.5 rounded-md transition-all duration-200 ${rating === 'down'
                    ? 'text-red-400 bg-red-50 dark:bg-red-500/10'
                    : error
                        ? 'text-amber-500 bg-amber-50 dark:bg-amber-500/10'
                        : 'text-gray-300 dark:text-gray-600 hover:text-red-400 hover:bg-red-50/50 dark:hover:bg-red-500/10'
                    }`}
                title={error ? "Saved locally. Backend feedback sync failed." : "Not helpful"}
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
    // 1. Get/create a thread_id per agent.
    // 2. Immediately restore browser-saved messages on refresh.
    // 3. Then try backend history without letting the loading overlay hang forever.
    useEffect(() => {
        if (!agentConfig?.id) return;

        let isMounted = true;

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

            if (!isMounted) return;
            setThreadId(currentThreadId);

            const localMessages = loadLocalMessages(agentConfig.id, currentThreadId);
            const localFeedback = loadLocalFeedback(agentConfig.id, currentThreadId);

            if (Object.keys(localFeedback).length > 0) {
                setFeedbackMap(localFeedback);
            }

            if (localMessages.length > 0) {
                setMessages(localMessages);
            }

            try {
                if (isExistingSession) {
                    const response = await fetchWithTimeout(
                        getHistoryEndpoint(agentConfig.id, currentThreadId),
                        {},
                        HISTORY_TIMEOUT_MS
                    );

                    if (!response.ok) {
                        throw new Error(`Failed to fetch history: ${response.status}`);
                    }

                    const data = await response.json();
                    const backendMessages = Array.isArray(data.messages)
                        ? data.messages.map(mapHistoryMessage)
                        : [];

                    if (backendMessages.length > 0) {
                        setMessages(backendMessages);
                        saveLocalMessages(agentConfig.id, currentThreadId, backendMessages);
                    } else if (localMessages.length === 0) {
                        setMessages([]);
                    }

                    try {
                        const fbRes = await fetchWithTimeout(
                            `${API_URL}/api/v1/feedback/${agentConfig.id}/${currentThreadId}`,
                            {},
                            HISTORY_TIMEOUT_MS
                        );

                        if (fbRes.ok) {
                            const fbData = await fbRes.json();
                            const userId = user.username || "anonymous";
                            const map = {};

                            for (const [idx, users] of Object.entries(fbData.feedback || {})) {
                                if (users[userId]) {
                                    map[idx] = users[userId];
                                }
                            }

                            setFeedbackMap(prev => ({ ...prev, ...map }));
                            saveLocalFeedback(agentConfig.id, currentThreadId, { ...localFeedback, ...map });
                        }
                    } catch (fbErr) {
                        console.error("Error fetching feedback:", fbErr);
                    }
                } else {
                    setMessages([]);
                }
            } catch (error) {
                console.error("Error fetching history:", error);

                // Keep locally restored chat on refresh. Do not wipe it just because
                // backend history failed or MCP history is not available.
                if (localMessages.length === 0) {
                    setMessages([]);
                }
            } finally {
                if (isMounted) {
                    setIsLoadingHistory(false);
                }
            }
        };

        loadAgentState();

        return () => {
            isMounted = false;
        };
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

    // Browser backup for refresh persistence.
    // The backend history endpoint is still the source of truth when available,
    // but local storage keeps refreshes safe if history loading fails.
    useEffect(() => {
        if (!agentConfig?.id || !threadId || isLoadingHistory) return;
        saveLocalMessages(agentConfig.id, threadId, messages);
    }, [agentConfig?.id, threadId, messages, isLoadingHistory]);


    // True when no exchange has happened yet (only the seeded greeting message exists).
    // Declared here so the observer effects below can react to mode changes.
    const isIdleForEffects = messages.length === 0 && !isLoadingHistory && !isLoading;

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
    const lastTextPresent = !!messages[messages.length - 1]?.text || (messages[messages.length - 1]?.productCards?.length > 0);
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
        anchorPendingRef.current = true;

        const controller = new AbortController();
        abortControllerRef.current = controller;
        let botMessageAdded = false;

        const chatEndpoint = getChatEndpoint();

        try {
            const response = await fetch(chatEndpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    agent_id: agentConfig.id,
                    user_id: user.username || "anonymous",
                    thread_id: threadId,
                }),
                signal: controller.signal,
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const contentType = response.headers.get('content-type') || '';

            setMessages(prev => [...prev, {
                type: 'bot',
                text: "",
                formType: null,
                productCards: [],
                productCardDisplay: null,
                timestamp: Date.now()
            }]);
            botMessageAdded = true;

            if (contentType.includes('application/json')) {
                const data = await response.json();
                const botMessage = buildBotMessageFromJsonResponse(data);

                let currentFormType = null;
                let cleanText = botMessage.text;
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
                        formType: currentFormType || botMessage.formType || newMessages[lastIdx].formType,
                        formData: botMessage.formData || newMessages[lastIdx].formData || null,
                        productCards: botMessage.productCards || [],
                        productCardDisplay: botMessage.productCardDisplay || newMessages[lastIdx].productCardDisplay || null,
                    };
                    return newMessages;
                });

                return;
            }

            if (!response.body) {
                throw new Error('Response body is empty or not streamable');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulatedText = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, { stream: true }).replace(/\r/g, '');
                accumulatedText = appendChunkSmartly(accumulatedText, chunk);

                let currentFormType = null;
                const embedded = extractEmbeddedProductCardsFromText(accumulatedText);
                let cleanText = embedded.text;

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
                    const existingCards = Array.isArray(newMessages[lastIdx].productCards) ? newMessages[lastIdx].productCards : [];
                    const productCards = prepareProductCardsForDisplay([...existingCards, ...embedded.productCards]);
                    newMessages[lastIdx] = {
                        ...newMessages[lastIdx],
                        text: cleanText,
                        formType: currentFormType || newMessages[lastIdx].formType,
                        productCards,
                        productCardDisplay: mergeProductDisplay(
                            newMessages[lastIdx].productCardDisplay,
                            embedded.productCardDisplay,
                            normalizeProductDisplay(null, productCards.length),
                        ),
                    };
                    return newMessages;
                });
            }

            // Some MCP proxies stream one JSON object as text/SSE. If the final
            // streamed value is JSON, rebuild the bot message so product image
            // cards render instead of showing raw JSON or image URLs.
            const parsedStreamJson = tryParseJsonPayload(accumulatedText);
            if (parsedStreamJson) {
                const botMessage = buildBotMessageFromJsonResponse(parsedStreamJson);

                let currentFormType = null;
                let cleanText = botMessage.text;
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
                        formType: currentFormType || botMessage.formType || newMessages[lastIdx].formType,
                        formData: botMessage.formData || newMessages[lastIdx].formData || null,
                        productCards: botMessage.productCards || [],
                        productCardDisplay: botMessage.productCardDisplay || newMessages[lastIdx].productCardDisplay || null,
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
                        productCards: [],
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
            if (m.type === 'user' || m.text || m.formType || (Array.isArray(m.productCards) && m.productCards.length > 0)) return i;
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

    const disclaimerText = null; // Set to null to hide disclaimer. Uncomment the block below to restore it.
    /*
    const disclaimerText = (
        <p className="text-center text-[0.65rem] text-gray-400 dark:text-gray-500 mt-2 font-light px-2">
            {agentConfig.disclaimer}
        </p>
    );
    */

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
                    className="flex-1 flex flex-col items-center justify-center px-4 pb-24 relative"
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
                        className="text-base sm:text-lg text-gray-500 dark:text-gray-400 mt-2 font-light text-center max-w-2xl"
                    >
                        {agentConfig.idlePrompt || "How can I help you today?"}
                    </motion.p>
                    <motion.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.5, delay: 0.2 }}
                        className="w-full max-w-[720px] mt-10 flex flex-col items-center"
                    >
                        {renderComposer(true)}
                        {disclaimerText}
                    </motion.div>

                    {/* Branding footer absolutely positioned at the bottom of the screen */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.3 }}
                        className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center justify-center gap-1.5 pointer-events-auto cursor-default select-none z-20"
                    >
                        <span className="text-[0.65rem] uppercase tracking-wider font-semibold text-gray-500 dark:text-gray-400">Powered by</span>
                        <img src={embryoLogo} alt="Embryo Logo" className="h-[20px] w-auto object-contain dark:brightness-110" />
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
                                    if (!(msg.type === 'user' || msg.text || msg.formType || (Array.isArray(msg.productCards) && msg.productCards.length > 0))) return null;
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
                                    const productCards = Array.isArray(msg.productCards) ? msg.productCards : [];
                                    const productCardDisplay = msg.productCardDisplay || normalizeProductDisplay(null, productCards.length);

                                    const markdownComponents = {
                                        p: ({ node, ...props }) => <p className="mb-3 last:mb-0" {...props} />,
                                        a: ({ node, ...props }) => <a className="text-blue-600 dark:text-blue-400 hover:underline" target="_blank" rel="noopener noreferrer" {...props} />,
                                        img: ({ node, ...props }) => (
                                            <img
                                                {...props}
                                                alt={props.alt || "LifeStore product image"}
                                                loading="lazy"
                                                referrerPolicy="no-referrer"
                                                onError={(e) => {
                                                    e.currentTarget.style.display = "none";
                                                }}
                                                className="my-4 max-w-[260px] rounded-2xl border border-gray-200 dark:border-gray-700 bg-white object-contain shadow-sm"
                                            />
                                        ),
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
                                        td: ({ node, ...props }) => <td className="px-4 py-2 border-b border-gray-100 dark:border-gray-800 border-r last:border-r-0 text-gray-600 dark:text-gray-300" {...props} />,
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
                                                        {sanitizeMarkdownBold(normalizeAssistantMarkdown(mainText))}
                                                    </ReactMarkdown>
                                                    <ProductCards products={productCards} color={agentConfig.color} display={productCardDisplay} />
                                                    {isStreamingThisMsg && (
                                                        <span className="inline-block align-middle w-[3px] h-4 bg-gray-500/70 dark:bg-gray-300/70 ml-0.5 rounded-sm animate-pulse" />
                                                    )}
                                                    <SourcesSection sources={sources} color={agentConfig.color} />
                                                    {msg.formType === 'lifestore' && (
                                                        <LifestoreForm initialProduct={msg.formData?.product || productCards[0]?.name || ''} />
                                                    )}
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
                                                            onFeedback={(idx, rating) => {
                                                                setFeedbackMap(prev => {
                                                                    const next = { ...prev };
                                                                    if (rating) next[idx] = rating;
                                                                    else delete next[idx];
                                                                    saveLocalFeedback(agentConfig.id, threadId, next);
                                                                    return next;
                                                                });
                                                            }}
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

                                {isLoading && (messages.length === 0 || messages[messages.length - 1].type === 'user' || (!messages[messages.length - 1].text && !messages[messages.length - 1].formType && !(messages[messages.length - 1].productCards && messages[messages.length - 1].productCards.length))) && (
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
                        <div className="w-full max-w-[820px] flex flex-col items-center">
                            {renderComposer()}
                            {disclaimerText}
                            <div className="mt-3.5 flex items-center justify-center gap-1.5 pointer-events-auto cursor-default select-none">
                                <span className="text-[0.65rem] uppercase tracking-wider font-semibold text-gray-500 dark:text-gray-400">Powered by</span>
                                <img src={embryoLogo} alt="Embryo Logo" className="h-[20px] w-auto object-contain dark:brightness-110" />
                            </div>
                        </div>
                    </div>
                </motion.div>
            )}
        </motion.div>
    );
});

ChatInterface.displayName = 'ChatInterface';

export default ChatInterface;
