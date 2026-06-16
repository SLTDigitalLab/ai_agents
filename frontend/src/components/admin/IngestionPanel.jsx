import React, { useState, useEffect, useRef } from 'react';
import { useMsal } from '@azure/msal-react';
import { AGENTS } from '../../config/agents';
import { motion, AnimatePresence } from 'framer-motion';
import { InteractionRequiredAuthError } from '@azure/msal-browser';
import { graphTokenRequest } from '../../authConfig';
import AdminLayout from './AdminLayout';
import { createPortal } from 'react-dom';

const API_BASE = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/v1/admin`;

const AGENT_TITLE = {};
Object.values(AGENTS).forEach(cfg => { AGENT_TITLE[cfg.id] = cfg.title; });

const getFriendlyIngestionMessage = (message, source = 'generic') => {
    const raw = String(message || '');
    const msg = raw.toLowerCase();
    const normalizedSource = String(source || '').toLowerCase();

    const isSharePoint = normalizedSource.includes('sharepoint');
    const isOneDrive = normalizedSource.includes('onedrive');

    const folderLabel = isSharePoint
        ? 'SharePoint site or folder'
        : isOneDrive
            ? 'OneDrive folder'
            : 'Microsoft folder/location';

    if (
        msg.includes('401') ||
        msg.includes('unauthorized') ||
        msg.includes('invalid token') ||
        msg.includes('token expired')
    ) {
        return isSharePoint
            ? 'Microsoft Graph token is invalid or expired. Please paste a fresh Graph API token that can read this SharePoint site or folder.'
            : 'Microsoft access token is invalid or expired. Please sign in again, or paste a fresh Graph API token.';
    }

    if (
        msg.includes('403') ||
        msg.includes('forbidden') ||
        msg.includes('access denied') ||
        msg.includes('does not have sufficient privileges')
    ) {
        return isSharePoint
            ? 'You do not have permission to access this SharePoint site or folder. Make sure the Graph token belongs to an account that has access to this SharePoint site and Documents library. If required, use a token with Files.Read.All or Sites.Read.All permission.'
            : 'You do not have permission to access this OneDrive folder. Use a Folder ID from your own OneDrive account, or make sure the folder is shared with your logged-in Microsoft account. For another user’s OneDrive folder, use Paste Graph token mode with a token from an account that has access.';
    }

    if (
        msg.includes('404') ||
        msg.includes('not found') ||
        msg.includes('itemnotfound') ||
        msg.includes('resource could not be found')
    ) {
        return isSharePoint
            ? 'SharePoint site or folder not found. Please check the SharePoint Site URL and Folder Path. Use the site root URL only, not a folder or SitePages URL.'
            : 'OneDrive folder not found. Please check whether the Folder ID is correct. Use a Folder ID from your own OneDrive account or from a folder shared with your logged-in Microsoft account.';
    }

    if (
        msg.includes('max retries exceeded') ||
        msg.includes('too many 500 error responses') ||
        msg.includes('responseerror') ||
        msg.includes('graph.microsoft.com')
    ) {
        return isSharePoint
            ? 'Microsoft Graph could not access this SharePoint site or folder. Please check the SharePoint Site URL, Folder Path, and token permissions. If the details are correct, wait a few minutes and try again.'
            : 'Microsoft Graph could not access this OneDrive folder. Please enter a valid Folder ID from your own OneDrive account, or from a folder shared with your logged-in Microsoft account. If you are using another user’s OneDrive folder, use Paste Graph token mode with a token from an account that has access.';
    }

    if (
        msg.includes('500') ||
        msg.includes('internal server error') ||
        msg.includes('server error')
    ) {
        return `The server failed while processing the ${folderLabel}. Please check the input details and backend logs, then try again.`;
    }

    if (
        msg.includes('failed to fetch') ||
        msg.includes('networkerror') ||
        msg.includes('network')
    ) {
        return 'Cannot connect to the backend server. Please check whether the backend is running.';
    }

    return raw || `Ingestion failed. Please check the ${folderLabel} access and try again.`;
};

const getWebsiteUrlError = (value) => {
    const raw = String(value || '').trim();

    if (!raw) return '';

    try {
        const url = new URL(raw);

        if (!['http:', 'https:'].includes(url.protocol)) {
            return 'Enter a valid URL starting with http:// or https://.';
        }

        if (!url.hostname || !url.hostname.includes('.')) {
            return 'Enter a valid website URL.';
        }

        if (/\s/.test(raw)) {
            return 'URL should not contain spaces.';
        }

        return '';
    } catch {
        return 'Enter a valid URL starting with http:// or https://.';
    }
};

const getSharePointSiteUrlError = (value) => {
    const raw = String(value || '').trim();

    if (!raw) return '';

    let url;

    try {
        url = new URL(raw);
    } catch {
        return 'Enter a valid SharePoint site URL.';
    }

    if (url.protocol !== 'https:') {
        return 'SharePoint site URL must start with https://.';
    }

    if (!url.hostname.toLowerCase().endsWith('.sharepoint.com')) {
        return 'Enter a valid SharePoint URL ending with sharepoint.com.';
    }

    if (/\s/.test(raw)) {
        return 'SharePoint URL should not contain spaces.';
    }

    const decodedPath = decodeURIComponent(url.pathname || '').replace(/\/+$/, '');
    const lowerPath = decodedPath.toLowerCase();

    if (!decodedPath || decodedPath === '/') {
        return 'Enter the full SharePoint site URL, for example https://tenant.sharepoint.com/sites/SiteName.';
    }

    const blockedSegments = [
        '/shared documents',
        '/forms/',
        '/sitepages/',
        '/_layouts/',
        '/lists/',
        '/documents/',
        'allitems.aspx',
    ];

    if (blockedSegments.some(segment => lowerPath.includes(segment)) || lowerPath.endsWith('.aspx')) {
        return 'Paste the SharePoint site URL only, not a page, document library, or folder URL.';
    }

    const parts = decodedPath.split('/').filter(Boolean);
    const firstPart = parts[0]?.toLowerCase();

    const isSitesOrTeamsUrl =
        (firstPart === 'sites' || firstPart === 'teams') &&
        parts.length === 2;

    const isRootLevelSiteUrl = parts.length === 1;

    if (!isSitesOrTeamsUrl && !isRootLevelSiteUrl) {
        return 'Enter the SharePoint site root URL only, for example https://tenant.sharepoint.com/sites/SiteName.';
    }

    return '';
};

const getSharePointFolderPathError = (value) => {
    const raw = String(value || '').trim();

    if (!raw) return '';

    const lower = raw.toLowerCase();

    if (lower.startsWith('http://') || lower.startsWith('https://') || lower.includes('.sharepoint.com')) {
        return 'Enter the folder path only, not the full SharePoint URL.';
    }

    if (raw.startsWith('/') || raw.endsWith('/')) {
        return 'Folder path should not start or end with a slash.';
    }

    if (raw.includes('\\')) {
        return 'Use forward slash / for nested folders, not backslash \\.';
    }

    if (raw.includes('//')) {
        return 'Folder path should not contain double slashes.';
    }

    if (/\s{2,}/.test(raw)) {
        return 'Folder path should not contain repeated spaces.';
    }

    if (/[<>:"|?*]/.test(raw)) {
        return 'Folder path contains invalid characters.';
    }

    return '';
};
// Parse VITE_ADMIN_AGENT_MAP — JSON of { email: [agent_id, ...] }, ["*"] means all.
let ADMIN_AGENT_MAP = {};
try {
    ADMIN_AGENT_MAP = JSON.parse(import.meta.env.VITE_ADMIN_AGENT_MAP || '{}');
} catch (e) {
    console.error('VITE_ADMIN_AGENT_MAP is not valid JSON', e);
}

const allowedAgentsFor = (email) => {
    if (!email) return [];
    return ADMIN_AGENT_MAP[email.toLowerCase()] || [];
};

const formatElapsed = (startedAt) => {
    if (!startedAt) return '';
    const sec = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000);
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s}s`;
};

const ALL_AGENTS = Object.values(AGENTS).map(cfg => ({
    id: cfg.id,
    title: cfg.title,
}));

// ── Status Toast ──────────────────────────────────────────────────────
const StatusToast = ({ status, onClose }) => {
    if (!status) return null;

    const isSuccess = status.type === 'success';
    const isError = status.type === 'error';

    const tone = isSuccess
        ? {
            card: 'bg-emerald-600 border-emerald-500',
            icon: 'bg-white/20 text-white',
            message: 'text-emerald-50',
            chip: 'bg-white/15 border-white/20 text-white',
        }
        : isError
            ? {
                card: 'bg-red-600 border-red-500',
                icon: 'bg-white/20 text-white',
                message: 'text-red-50',
                chip: 'bg-white/15 border-white/20 text-white',
            }
            : {
                card: 'bg-amber-500 border-amber-400',
                icon: 'bg-white/25 text-white',
                message: 'text-amber-50',
                chip: 'bg-white/20 border-white/25 text-white',
            };

    const icon = isSuccess ? '✓' : isError ? '!' : '⚠';

    return createPortal(
        <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.98 }}
            transition={{ duration: 0.25 }}
            className="fixed bottom-6 right-6 z-[99999] w-[460px] max-w-[calc(100vw-2rem)]"
        >
            <div className={`max-h-[70vh] overflow-y-auto rounded-3xl border ${tone.card} p-5 shadow-2xl shadow-slate-950/40`}>
                <div className="flex items-start gap-4">
                    <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ${tone.icon} text-lg font-extrabold`}>
                        {icon}
                    </div>

                    <div className="min-w-0 flex-1">
                        <p className="text-base font-extrabold text-white">
                            {status.title}
                        </p>

                        <p className={`mt-1 text-sm font-medium leading-relaxed ${tone.message}`}>
                            {status.message}
                        </p>

                        {status.files && status.files.length > 0 && (
                            <div className="mt-3 flex max-h-28 flex-wrap gap-1.5 overflow-y-auto pr-1">
                                {status.files.map((file, index) => (
                                    <span
                                        key={`${file}-${index}`}
                                        className={`max-w-full truncate rounded-full border px-2.5 py-1 text-[11px] font-semibold ${tone.chip}`}
                                    >
                                        {file}
                                    </span>
                                ))}
                            </div>
                        )}
                    </div>

                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-xl p-1.5 text-white/70 transition-all hover:bg-white/15 hover:text-white"
                        aria-label="Close notification"
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            fill="none"
                            viewBox="0 0 24 24"
                            strokeWidth={2}
                            stroke="currentColor"
                            className="h-4 w-4"
                        >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>
            </div>
        </motion.div>,
        document.body
    );
};


// ── Main Ingestion Panel ──────────────────────────────────────────────
const IngestionPanel = () => {
    const { instance, accounts } = useMsal();
    const userEmail = accounts[0]?.username || '';

    // Filter agent list to only those this user is authorised for.
    // ["*"] means super-admin → show every agent.
    const allowed = allowedAgentsFor(userEmail);
    const AGENT_LIST = allowed.includes('*')
        ? ALL_AGENTS
        : ALL_AGENTS.filter(a => allowed.includes(a.id));

    const [activeTab, setActiveTab] = useState('url');
    const [status, setStatus] = useState(null);

    const defaultAgent = AGENT_LIST[0]?.id || '';
    const financeAgent = AGENT_LIST.find(a => a.id === 'finance')?.id || defaultAgent;

    // URL Ingestion state
    const [urlForm, setUrlForm] = useState({ url: '', agent_name: defaultAgent });
    const [urlLoading, setUrlLoading] = useState(false);

    // OneDrive Ingestion state
    const [odForm, setOdForm] = useState({ folder_id: '', token: '', agent_name: defaultAgent });
    const [odAuthMode, setOdAuthMode] = useState('auto'); // auto | manual
    const [odLoading, setOdLoading] = useState(false);

    // SharePoint Ingestion state
    const [spForm, setSpForm] = useState({
        site_url: '',
        folder_path: '',
        token: '',
        agent_name: financeAgent,
        force: false,
    });
    const [spLoading, setSpLoading] = useState(false);

    // Server-tracked ingestion status (survives page refresh)
    const [serverStatus, setServerStatus] = useState(null);
    const [tick, setTick] = useState(0); // re-render every second to update elapsed time
    const lastSeenResultRef = useRef(null);
    const hasInitializedStatusRef = useRef(false);

    useEffect(() => {
        let cancelled = false;
        const poll = async () => {
            try {
                const res = await fetch(`${API_BASE}/ingestion-status`);
                if (!res.ok) return;
                const data = await res.json();
                if (cancelled) return;
                setServerStatus(data);

                // Surface last_result as a toast when it changes (survives refresh)
                // Surface last_result as a toast only for new results.
                // On first page load, remember the existing last_result but do not show it again.
                const stamp = data.last_result?.finished_at;

                if (!hasInitializedStatusRef.current) {
                    hasInitializedStatusRef.current = true;
                    lastSeenResultRef.current = stamp || null;
                    return;
                }

                if (stamp && stamp !== lastSeenResultRef.current) {
                    lastSeenResultRef.current = stamp;
                    const r = data.last_result;

                    setStatus({
                        type: r.status === 'success' ? 'success' : r.status === 'warning' ? 'warning' : 'error',
                        title: r.status === 'success' ? 'Ingestion Complete' : r.status === 'warning' ? 'Warning' : 'Error',
                        message: r.status === 'error'
                            ? getFriendlyIngestionMessage(r.message, r.source)
                            : r.message,
                        files: r.files || [],
                    });

                    setUrlLoading(false);
                    setOdLoading(false);
                    setSpLoading(false);
                }
            } catch {
                /* ignore network errors */
            }
        };
        poll();
        const id = setInterval(poll, 3000);
        return () => { cancelled = true; clearInterval(id); };
    }, []);

    useEffect(() => {
        if (!serverStatus?.active) return;
        const id = setInterval(() => setTick(t => t + 1), 1000);
        return () => clearInterval(id);
    }, [serverStatus?.active]);

    // ── URL Ingestion Handler ──
    const handleUrlIngest = async (e) => {
        e.preventDefault();

        const error = getWebsiteUrlError(urlForm.url);

        if (!urlForm.url.trim() || error) {
            setStatus({
                type: 'error',
                title: 'Invalid URL',
                message: error || 'Please enter a website URL.',
            });
            return;
        }

        setUrlLoading(true);
        setStatus(null);
        try {
            const res = await fetch(`${API_BASE}/ingest-url`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...urlForm, user_email: userEmail }),
            });
            const data = await res.json();

            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

            // Backend runs ingestion asynchronously now; a 'started' response
            // means the job was accepted. Progress + final result come from
            // the /ingestion-status poller above.
            if (data.status === 'started') {
                setStatus({
                    type: 'success',
                    title: 'Ingestion Started',
                    message: 'Running in the background — see progress banner.',
                });
                setUrlForm(prev => ({ ...prev, url: '' }));
            } else {
                setStatus({
                    type: data.status === 'success' ? 'success' : 'warning',
                    title: data.status === 'success' ? 'Ingestion Complete' : 'Warning',
                    message: data.message,
                });
                if (data.status === 'success') setUrlForm(prev => ({ ...prev, url: '' }));
            }
        } catch (err) {
            setStatus({ type: 'error', title: 'Ingestion Failed', message: err.message });
            setUrlLoading(false);
        }
    };

    const getGraphAccessToken = async () => {
        const account = instance.getActiveAccount() || accounts[0];

        if (!account) {
            throw new Error('No Microsoft account found. Please sign in again.');
        }

        const request = {
            ...graphTokenRequest,
            account,
        };

        try {
            const response = await instance.acquireTokenSilent(request);
            return response.accessToken;
        } catch (err) {
            const needsInteraction =
                err instanceof InteractionRequiredAuthError ||
                err.errorCode === 'interaction_required' ||
                err.errorCode === 'consent_required' ||
                err.errorCode === 'login_required';

            if (needsInteraction) {
                const response = await instance.acquireTokenPopup(request);
                return response.accessToken;
            }

            throw err;
        }
    };

    // ── OneDrive Ingestion Handler ──
    const handleOneDriveIngest = async (e) => {
        e.preventDefault();

        if (!odForm.folder_id.trim()) return;

        if (odAuthMode === 'manual' && !odForm.token.trim()) {
            setStatus({
                type: 'error',
                title: 'Token Required',
                message: 'Please paste a Graph API access token or switch to automatic logged-in account mode.',
            });
            return;
        }

        setOdLoading(true);
        setStatus(null);

        try {
            const token =
                odAuthMode === 'manual'
                    ? odForm.token.trim()
                    : await getGraphAccessToken();

            const payload = {
                ...odForm,
                token,
            };

            const res = await fetch(`${API_BASE}/ingest-onedrive`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...payload, user_email: userEmail }),
            });

            const data = await res.json();

            if (!res.ok) {
                throw new Error(
                    getFriendlyIngestionMessage(data.detail || data.message || `HTTP ${res.status}`, 'onedrive')
                );
            }

            if (data.status === 'started') {
                setStatus({
                    type: 'success',
                    title: 'Ingestion Started',
                    message: 'Running in the background — see progress banner.',
                });

                setOdForm(prev => ({ ...prev, folder_id: '', token: '' }));
            } else {
                setStatus({
                    type: data.status === 'success' ? 'success' : data.status === 'warning' ? 'warning' : 'error',
                    title: data.status === 'success' ? 'Ingestion Complete' : data.status === 'warning' ? 'Warning' : 'Error',
                    message: data.message,
                    files: data.files || [],
                });

                if (data.status === 'success') {
                    setOdForm(prev => ({ ...prev, folder_id: '', token: '' }));
                }
            }
        } catch (err) {
            setStatus({
                type: 'error',
                title: 'OneDrive Ingestion Failed',
                message: err.message || 'Failed to get Microsoft Graph token or ingest OneDrive folder.',
            });
            setOdLoading(false);
        }
    };

    // ── SharePoint Ingestion Handler ──
    const handleSharePointIngest = async (e) => {
        e.preventDefault();

        if (
            !spForm.site_url.trim() ||
            !spForm.folder_path.trim() ||
            !spForm.token.trim()
        ) {
            return;
        }

        const siteUrlError = getSharePointSiteUrlError(spForm.site_url);
        const folderPathError = getSharePointFolderPathError(spForm.folder_path);

        if (siteUrlError) {
            setStatus({
                type: 'error',
                title: 'Invalid SharePoint URL',
                message: siteUrlError,
            });
            return;
        }

        if (folderPathError) {
            setStatus({
                type: 'error',
                title: 'Invalid Folder Path',
                message: folderPathError,
            });
            return;
        }

        setSpLoading(true);
        setStatus(null);

        try {
            const res = await fetch(`${API_BASE}/ingest-sharepoint`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ...spForm,
                    site_url: spForm.site_url.trim(),
                    folder_path: spForm.folder_path.trim(),
                    token: spForm.token.trim(),
                    user_email: userEmail,
                }),
            });

            const data = await res.json();

            if (!res.ok) {
                throw new Error(
                    getFriendlyIngestionMessage(data.detail || data.message || `HTTP ${res.status}`, 'sharepoint')
                );
            }

            if (data.status === 'started') {
                setStatus({
                    type: 'success',
                    title: 'SharePoint Ingestion Started',
                    message: 'Running in the background — see progress banner.',
                });

                // Keep IDs for repeated testing. Clear only token.
                setSpForm(prev => ({ ...prev, token: '' }));
            } else {
                setStatus({
                    type: data.status === 'success' ? 'success' : data.status === 'warning' ? 'warning' : 'error',
                    title: data.status === 'success' ? 'Ingestion Complete' : data.status === 'warning' ? 'Warning' : 'Error',
                    message: data.message,
                    files: data.files || [],
                });

                if (data.status === 'success') {
                    setSpForm(prev => ({ ...prev, token: '' }));
                }
            }
        } catch (err) {
            setStatus({
                type: 'error',
                title: 'SharePoint Ingestion Failed',
                message: err.message,
            });
            setSpLoading(false);
        }
    };

    const websiteUrlError = getWebsiteUrlError(urlForm.url);
    const sharePointSiteUrlError = getSharePointSiteUrlError(spForm.site_url);
    const sharePointFolderPathError = getSharePointFolderPathError(spForm.folder_path);

    const isUrlSubmitDisabled =
        urlLoading ||
        !urlForm.url.trim() ||
        Boolean(websiteUrlError);

    const isOneDriveSubmitDisabled =
        odLoading ||
        !odForm.folder_id.trim() ||
        (odAuthMode === 'manual' && !odForm.token.trim());

    const isSharePointSubmitDisabled =
        spLoading ||
        !spForm.site_url.trim() ||
        !spForm.folder_path.trim() ||
        !spForm.token.trim() ||
        Boolean(sharePointSiteUrlError) ||
        Boolean(sharePointFolderPathError);

    const fieldClass =
        'w-full bg-white/[0.04] border border-white/[0.10] text-white rounded-2xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-400/50 transition-all placeholder:text-white/25';

    const fieldErrorClass =
        'w-full bg-red-500/10 border border-red-500/40 text-white rounded-2xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-red-500/40 focus:border-red-400 transition-all placeholder:text-white/25';

    const primaryButtonClass =
        'w-full flex items-center justify-center gap-2 bg-[#4c1d95] hover:bg-[#5b21b6] text-white font-bold py-3.5 rounded-2xl transition-all disabled:bg-[#4c1d95]/70 disabled:text-white/45 disabled:cursor-not-allowed shadow-lg shadow-purple-900/30';

    const tabs = [
        { key: 'url', label: 'URL Ingestion', icon: '🌐' },
        { key: 'onedrive', label: 'OneDrive Sync', icon: '☁️' },
        { key: 'sharepoint', label: 'SharePoint Sync', icon: '🟦' },
    ];

    return (
        <AdminLayout
            title="Data Ingestion"
            subtitle="Ingest website URLs, OneDrive documents, and SharePoint documents into agent knowledge bases."
            backTo="/admin"
            backLabel="Back to Dashboard"
            backgroundVariant="legacy-dark"
        >

            <div className="relative z-10 max-w-5xl mx-auto">

                {/* ── Active Ingestion Banner (survives page refresh) ── */}
                <AnimatePresence>
                    {serverStatus?.active && (
                        <motion.div
                            key={`ingesting-${tick}`}
                            initial={{ opacity: 0, y: -8 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -8 }}
                            className="mb-6 flex items-center gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/[0.08] p-4"
                        >
                            <div className="w-5 h-5 border-2 border-amber-400/40 border-t-amber-300 rounded-full animate-spin" />
                            <div className="flex-1">
                                <p className="text-amber-200 text-sm font-semibold">
                                    Ingestion in progress — {AGENT_TITLE[serverStatus.agent_name] || serverStatus.agent_name}
                                </p>
                                <p className="text-amber-200/60 text-xs mt-0.5">
                                    Source: {serverStatus.source} · Elapsed: {formatElapsed(serverStatus.started_at)} · Do not start another ingestion
                                </p>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* ── Tab Switcher ─────────────────────────────── */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                    className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6 rounded-3xl border border-white/[0.08] bg-white/[0.03] p-2 shadow-xl shadow-slate-950/25 backdrop-blur-sm"
                >
                    {tabs.map(tab => (
                        <button
                            key={tab.key}
                            onClick={() => setActiveTab(tab.key)}
                            className={`flex items-center justify-center gap-2 px-5 py-3 rounded-2xl text-sm font-bold transition-all duration-200 ${
                                activeTab === tab.key
                                    ? 'bg-white/[0.08] text-white border border-white/[0.18] shadow-lg shadow-slate-950/20'
                                    : 'bg-white/[0.03] text-white/45 border border-white/[0.08] hover:bg-white/[0.06] hover:text-white'
                            }`}
                        >
                            <span>{tab.icon}</span>
                            {tab.label}
                        </button>
                    ))}
                </motion.div>

                {/* ── URL Ingestion Form ──────────────────────── */}
                <AnimatePresence mode="wait">
                    {activeTab === 'url' && (
                        <motion.div
                            key="url"
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: 20 }}
                            transition={{ duration: 0.2 }}
                        >
                            <form onSubmit={handleUrlIngest} className="rounded-[28px] border border-white/[0.08] bg-white/[0.03] p-6 sm:p-8 space-y-6 shadow-2xl shadow-slate-950/25 text-white backdrop-blur-sm">
                                <div>
                                    <h2 className="text-white font-bold text-xl mb-1">Ingest from URL</h2>
                                    <p className="text-white/45 text-sm">Scrape a web page, split into chunks, and embed into the agent's knowledge base</p>
                                </div>

                                {/* Agent Select */}
                                <div>
                                    <label htmlFor="url-agent" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        Target Agent
                                    </label>
                                    <select
                                        id="url-agent"
                                        value={urlForm.agent_name}
                                        onChange={(e) => setUrlForm(prev => ({ ...prev, agent_name: e.target.value }))}
                                        className={fieldClass}
                                    >
                                        {AGENT_LIST.map(a => (
                                            <option key={a.id} value={a.id} className="bg-slate-900 text-white">{a.title}</option>
                                        ))}
                                    </select>
                                </div>

                                {/* URL Input */}
                                <div>
                                    <label htmlFor="url-input" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        Website URL
                                    </label>

                                    {urlForm.url.trim() && websiteUrlError && (
                                        <p className="text-red-300 text-xs mb-2">
                                            {websiteUrlError}
                                        </p>
                                    )}

                                    <input
                                        id="url-input"
                                        type="text"
                                        value={urlForm.url}
                                        onChange={(e) => setUrlForm(prev => ({ ...prev, url: e.target.value }))}
                                        placeholder="https://example.com/policy-document"
                                        required
                                        className={urlForm.url.trim() && websiteUrlError ? fieldErrorClass : fieldClass}
                                    />
                                </div>

                                {/* Submit */}
                                <button
                                    type="submit"
                                    disabled={isUrlSubmitDisabled}
                                    className={primaryButtonClass}
                                >
                                    {urlLoading ? (
                                        <>
                                            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                            Ingesting...
                                        </>
                                    ) : (
                                        <>
                                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                                            </svg>
                                            Ingest URL
                                        </>
                                    )}
                                </button>
                            </form>
                        </motion.div>
                    )}

                    {/* ── OneDrive Ingestion Form ────────────────── */}
                    {activeTab === 'onedrive' && (
                        <motion.div
                            key="onedrive"
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: 20 }}
                            transition={{ duration: 0.2 }}
                        >
                            <form onSubmit={handleOneDriveIngest} className="rounded-[28px] border border-white/[0.08] bg-white/[0.03] p-6 sm:p-8 space-y-6 shadow-2xl shadow-slate-950/25 text-white backdrop-blur-sm">
                                <div>
                                    <h2 className="text-white font-bold text-xl mb-1">Ingest from OneDrive</h2>
                                    <p className="text-white/45 text-sm">Download files from a OneDrive folder, chunk them semantically, and embed into the agent's knowledge base</p>
                                </div>

                                {/* Agent Select */}
                                <div>
                                    <label htmlFor="od-agent" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        Target Agent
                                    </label>
                                    <select
                                        id="od-agent"
                                        value={odForm.agent_name}
                                        onChange={(e) => setOdForm(prev => ({ ...prev, agent_name: e.target.value }))}
                                        className={fieldClass}
                                    >
                                        {AGENT_LIST.map(a => (
                                            <option key={a.id} value={a.id} className="bg-slate-900 text-white">{a.title}</option>
                                        ))}
                                    </select>
                                </div>

                                {/* Folder ID */}
                                <div>
                                    <label htmlFor="od-folder-id" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        OneDrive Folder ID
                                    </label>
                                    <input
                                        id="od-folder-id"
                                        type="text"
                                        value={odForm.folder_id}
                                        onChange={(e) => setOdForm(prev => ({ ...prev, folder_id: e.target.value }))}
                                        placeholder="e.g. 01ABCDEF23456789..."
                                        required
                                        className={fieldClass}
                                    />
                                    <p className="text-white/35 text-xs mt-1.5">
                                        The item ID of the OneDrive folder containing your documents (PDFs, DOCX, PPTX, XLSX, images)
                                    </p>
                                </div>

                                {/* OneDrive Auth Method */}
                                <div>
                                    <label className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        Access Method
                                    </label>

                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setOdAuthMode('auto');
                                                setOdForm(prev => ({ ...prev, token: '' }));
                                            }}
                                            className={`text-left rounded-2xl border p-4 transition-all ${
                                                odAuthMode === 'auto'
                                                    ? 'bg-cyan-400/10 border-cyan-400/30 text-white shadow-sm'
                                                    : 'bg-white/[0.03] border-white/[0.08] text-white/70 hover:bg-white/[0.06]'
                                            }`}
                                        >
                                            <span className="block text-sm font-semibold">Use logged-in account</span>
                                            <span className="block text-xs mt-1 text-white/45">
                                                Best for files in the currently signed-in user's OneDrive.
                                            </span>
                                        </button>

                                        <button
                                            type="button"
                                            onClick={() => setOdAuthMode('manual')}
                                            className={`text-left rounded-2xl border p-4 transition-all ${
                                                odAuthMode === 'manual'
                                                    ? 'bg-cyan-400/10 border-cyan-400/30 text-white shadow-sm'
                                                    : 'bg-white/[0.03] border-white/[0.08] text-white/70 hover:bg-white/[0.06]'
                                            }`}
                                        >
                                            <span className="block text-sm font-semibold">Paste Graph token</span>
                                            <span className="block text-xs mt-1 text-white/45">
                                                Use this for another account or a token copied from Graph Explorer.
                                            </span>
                                        </button>
                                    </div>
                                </div>

                                {odAuthMode === 'auto' ? (
                                    <div className="bg-cyan-400/10 border border-cyan-400/20 rounded-2xl p-4">
                                        <p className="text-cyan-100 text-xs font-bold mb-1">
                                            Token will be generated automatically
                                        </p>
                                        <p className="text-white/45 text-xs leading-relaxed">
                                            The app will request a Microsoft Graph token using the currently logged-in account.
                                            This can read only the OneDrive files/folders that logged-in account can access.
                                            If Microsoft asks for permission, approve the Files.Read permission.
                                        </p>
                                    </div>
                                ) : (
                                    <div>
                                        <label htmlFor="od-token" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                            Graph API Access Token
                                        </label>
                                        <textarea
                                            id="od-token"
                                            value={odForm.token}
                                            onChange={(e) => setOdForm(prev => ({ ...prev, token: e.target.value }))}
                                            placeholder="eyJ0eXAiOiJKV1QiLCJub..."
                                            required={odAuthMode === 'manual'}
                                            rows={3}
                                            className={fieldClass}
                                        />
                                        <p className="text-white/35 text-xs mt-1.5">
                                            Use a valid Microsoft Graph token from the account that can access this OneDrive folder.
                                        </p>
                                    </div>
                                )}

                                {/* Supported Files Info */}
                                <div className="bg-white/[0.03] border border-white/[0.08] rounded-2xl p-4">
                                    <p className="text-white/60 text-xs font-bold mb-2">Supported File Types</p>
                                    <div className="flex flex-wrap gap-1.5">
                                        {['.pdf', '.docx', '.pptx', '.xlsx', '.png', '.jpg', '.jpeg', '.eml'].map(ext => (
                                            <span key={ext} className="text-[11px] bg-white/[0.04] border border-white/[0.08] text-white/55 px-2.5 py-1 rounded-full font-mono">
                                                {ext}
                                            </span>
                                        ))}
                                    </div>
                                </div>

                                {/* Submit */}
                                <button
                                    type="submit"
                                    disabled={isOneDriveSubmitDisabled}
                                    className={primaryButtonClass}
                                >
                                    {odLoading ? (
                                        <>
                                            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                            Processing Files...
                                        </>
                                    ) : (
                                        <>
                                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
                                            </svg>
                                            Start OneDrive Ingestion
                                        </>
                                    )}
                                </button>
                            </form>
                        </motion.div>
                    )}

                    {/* ── SharePoint Ingestion Form ────────────────── */}
                    {activeTab === 'sharepoint' && (
                        <motion.div
                            key="sharepoint"
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: 20 }}
                            transition={{ duration: 0.2 }}
                        >
                            <form onSubmit={handleSharePointIngest} className="rounded-[28px] border border-white/[0.08] bg-white/[0.03] p-6 sm:p-8 space-y-6 shadow-2xl shadow-slate-950/25 text-white backdrop-blur-sm">
                                <div>
                                    <h2 className="text-white font-bold text-xl mb-1">Ingest from SharePoint</h2>
                                    <p className="text-white/45 text-sm">
                                        Download files from a SharePoint document library folder, chunk them semantically, and embed into the selected agent knowledge base
                                    </p>
                                </div>

                                <div>
                                    <label htmlFor="sp-agent" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        Target Agent
                                    </label>
                                    <select
                                        id="sp-agent"
                                        value={spForm.agent_name}
                                        onChange={(e) => setSpForm(prev => ({ ...prev, agent_name: e.target.value }))}
                                        className={fieldClass}
                                    >
                                        {AGENT_LIST.map(a => (
                                            <option key={a.id} value={a.id} className="bg-slate-900 text-white">{a.title}</option>
                                        ))}
                                    </select>
                                </div>
                                <div>
                                    <label htmlFor="sp-site-url" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        SharePoint Site URL
                                    </label>

                                    {spForm.site_url.trim() && sharePointSiteUrlError && (
                                        <p className="text-red-300 text-xs mb-2">
                                            {sharePointSiteUrlError}
                                        </p>
                                    )}

                                    <input
                                        id="sp-site-url"
                                        type="text"
                                        value={spForm.site_url}
                                        onChange={(e) => setSpForm(prev => ({ ...prev, site_url: e.target.value }))}
                                        placeholder="https://sltcomlk.sharepoint.com/sites/WorkmateAITestKB"
                                        required
                                        className={spForm.site_url.trim() && sharePointSiteUrlError ? fieldErrorClass : fieldClass}
                                    />
                                    <p className="text-white/35 text-xs mt-1.5">
                                        Paste the SharePoint site URL only.
                                    </p>
                                </div>

                                <div>
                                    <label htmlFor="sp-folder-path" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        Folder Path
                                    </label>

                                    {spForm.folder_path.trim() && sharePointFolderPathError && (
                                        <p className="text-red-300 text-xs mb-2">
                                            {sharePointFolderPathError}
                                        </p>
                                    )}

                                    <input
                                        id="sp-folder-path"
                                        type="text"
                                        value={spForm.folder_path}
                                        onChange={(e) => setSpForm(prev => ({ ...prev, folder_path: e.target.value }))}
                                        placeholder="AI-Ingestion-Test"
                                        required
                                        className={spForm.folder_path.trim() && sharePointFolderPathError ? fieldErrorClass : fieldClass}
                                    />

                                    <p className="text-white/35 text-xs mt-1.5">
                                        Folder path inside the default Documents library. Use nested paths like Finance/Restricted if needed.
                                    </p>
                                </div>

                                <div>
                                    <label htmlFor="sp-token" className="block text-white/45 text-xs uppercase tracking-wider font-bold mb-2">
                                        Graph API Access Token
                                    </label>
                                    <textarea
                                        id="sp-token"
                                        value={spForm.token}
                                        onChange={(e) => setSpForm(prev => ({ ...prev, token: e.target.value }))}
                                        placeholder="eyJ0eXAiOiJKV1QiLCJub..."
                                        required
                                        rows={3}
                                        className={fieldClass}
                                    />
                                    <p className="text-white/35 text-xs mt-1.5">
                                        A valid Microsoft Graph token that can read this SharePoint folder.
                                    </p>
                                </div>

                                <label className="flex items-center gap-3 bg-white/[0.03] border border-white/[0.08] rounded-2xl p-4 cursor-pointer hover:bg-white/[0.06] transition-all">
                                    <input
                                        type="checkbox"
                                        checked={spForm.force}
                                        onChange={(e) => setSpForm(prev => ({ ...prev, force: e.target.checked }))}
                                        className="h-4 w-4 rounded border-white/20 accent-cyan-400"
                                    />
                                    <span>
                                        <span className="block text-white text-sm font-bold">Force re-ingest</span>
                                            <span className="block text-white/45 text-xs">
                                            Re-process files even if they were already ingested and unchanged.
                                        </span>
                                    </span>
                                </label>

                                <div className="bg-white/[0.03] border border-white/[0.08] rounded-2xl p-4">
                                    <p className="text-white/60 text-xs font-bold mb-2">Supported File Types</p>
                                    <div className="flex flex-wrap gap-1.5">
                                        {['.pdf', '.docx', '.pptx', '.xlsx', '.png', '.jpg', '.jpeg', '.eml'].map(ext => (
                                            <span key={ext} className="text-[11px] bg-white/[0.04] border border-white/[0.08] text-white/55 px-2.5 py-1 rounded-full font-mono">
                                                {ext}
                                            </span>
                                        ))}
                                    </div>
                                </div>

                                <button
                                    type="submit"
                                    disabled={isSharePointSubmitDisabled}
                                    className={primaryButtonClass}
                                >
                                    {spLoading ? (
                                        <>
                                            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                            Processing SharePoint Files...
                                        </>
                                    ) : (
                                        <>
                                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
                                            </svg>
                                            Start SharePoint Ingestion
                                        </>
                                    )}
                                </button>
                            </form>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>

            {/* Status Toast */}
            <AnimatePresence>
                <StatusToast status={status} onClose={() => setStatus(null)} />
            </AnimatePresence>
        </AdminLayout>
    );
};

export default IngestionPanel;
