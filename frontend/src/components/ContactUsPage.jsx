import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useMsal, AuthenticatedTemplate, UnauthenticatedTemplate } from '@azure/msal-react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { loginRequest } from '../authConfig';
import sltLogo from '../assets/slt-mobitel-logo.png';
import embryoLogo from '../assets/embryo-removebg.png';
import { useTheme } from '../contexts/ThemeContext';

const API_BASE =
  import.meta.env.VITE_API_BASE ||
  `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/v1`;

const WORKMATE_COLOR = 'from-cyan-500 to-blue-600';

const getInitials = (name) => {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
  return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
};

const MicrosoftIcon = () => (
  <svg width="20" height="20" viewBox="0 0 21 21" fill="none">
    <rect x="1" y="1" width="9" height="9" fill="#F25022" />
    <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
    <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
    <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
  </svg>
);

const UserAvatarMenu = ({ user, onLogout }) => {
  const [open, setOpen] = useState(false);
  const { theme, setTheme } = useTheme();
  const menuRef = useRef(null);
  const initials = getInitials(user?.name || user?.username);

  useEffect(() => {
    if (!open) return;

    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpen(false);
      }
    };

    const onEsc = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };

    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);

    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);

  return (
    <div ref={menuRef} className="relative">
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, x: -8, scale: 0.96 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: -8, scale: 0.96 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            className="absolute bottom-0 left-full ml-3 w-64 bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-[0_20px_50px_-15px_rgba(0,0,0,0.18)] dark:shadow-[0_20px_50px_-15px_rgba(0,0,0,0.6)] overflow-hidden z-40"
          >
            <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-800 flex items-center gap-3">
              <div className={`w-9 h-9 rounded-full bg-gradient-to-br ${WORKMATE_COLOR} text-white text-sm font-semibold flex items-center justify-center shadow-sm shrink-0`}>
                {initials}
              </div>

              <div className="min-w-0">
                <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
                  {user.name || 'User'}
                </p>
                <p className="text-[0.7rem] text-gray-500 dark:text-gray-400 truncate">
                  {user.username || ''}
                </p>
              </div>
            </div>

            <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-800">
              <p className="text-[0.7rem] uppercase tracking-wider font-semibold text-gray-400 dark:text-gray-500 mb-2">
                Theme
              </p>

              <div className="grid grid-cols-2 gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
                {['light', 'dark'].map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setTheme(mode)}
                    className={`flex items-center justify-center py-1.5 rounded-md text-xs font-medium transition-all ${
                      theme === mode
                        ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm'
                        : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
                    }`}
                  >
                    {mode === 'light' ? 'Light' : 'Dark'}
                  </button>
                ))}
              </div>
            </div>

            <button
              type="button"
              onClick={onLogout}
              className="w-full flex items-center gap-2.5 px-4 py-3 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-gray-500 dark:text-gray-400">
                <path fillRule="evenodd" d="M3 4.25A2.25 2.25 0 015.25 2h5.5A2.25 2.25 0 0113 4.25v2a.75.75 0 01-1.5 0v-2a.75.75 0 00-.75-.75h-5.5a.75.75 0 00-.75.75v11.5c0 .414.336.75.75.75h5.5a.75.75 0 00.75-.75v-2a.75.75 0 011.5 0v2A2.25 2.25 0 0110.75 18h-5.5A2.25 2.25 0 013 15.75V4.25z" clipRule="evenodd" />
                <path fillRule="evenodd" d="M19 10a.75.75 0 00-.22-.53l-2.75-2.75a.75.75 0 10-1.06 1.06l1.47 1.47H8.75a.75.75 0 000 1.5h7.69l-1.47 1.47a.75.75 0 101.06 1.06l2.75-2.75A.75.75 0 0019 10z" clipRule="evenodd" />
              </svg>
              Logout
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <motion.button
        type="button"
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        onClick={() => setOpen((v) => !v)}
        title={user.name || user.username}
        className={`w-10 h-10 rounded-full bg-gradient-to-br ${WORKMATE_COLOR} text-white text-sm font-semibold flex items-center justify-center shadow-md hover:shadow-lg transition-shadow ring-2 ring-white dark:ring-gray-900`}
      >
        {initials}
      </motion.button>
    </div>
  );
};

const SidebarRail = ({ user, onLogout, onBackToChat }) => (
  <aside className="hidden sm:flex w-16 sm:w-[68px] shrink-0 flex-col items-center py-4 bg-white dark:bg-[#23272e] border-r border-gray-200/70 dark:border-[#33373f] z-30">
    <motion.button
      type="button"
      onClick={onBackToChat}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
      title="Back to Workmate AI"
      className="group/new flex items-center justify-center w-11 h-11 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
        <path fillRule="evenodd" d="M17 10a.75.75 0 01-.75.75H5.56l4.22 4.22a.75.75 0 11-1.06 1.06l-5.5-5.5a.75.75 0 010-1.06l5.5-5.5a.75.75 0 111.06 1.06L5.56 9.25h10.69A.75.75 0 0117 10z" clipRule="evenodd" />
      </svg>
    </motion.button>

    <div className="flex-1" />

    <UserAvatarMenu user={user} onLogout={onLogout} />
  </aside>
);

const StatusToast = ({ status, onClose }) => {
  if (!status) return null;

  const isSuccess = status.type === 'success';

  return (
    <motion.div
      initial={{ opacity: 0, x: 40, y: 20, scale: 0.96 }}
      animate={{ opacity: 1, x: 0, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 40, y: 20, scale: 0.96 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      className={`fixed bottom-6 right-6 z-[9999] w-[360px] max-w-[calc(100vw-2rem)] rounded-2xl border px-4 py-4 shadow-[0_20px_60px_-20px_rgba(0,0,0,0.6)] backdrop-blur-xl ${
        isSuccess
          ? 'bg-emerald-500/15 border-emerald-400/30 text-emerald-100'
          : 'bg-red-500/15 border-red-400/30 text-red-100'
      }`}
    >
      <div className="flex items-start gap-3">
        <div
          className={`mt-0.5 w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
            isSuccess
              ? 'bg-emerald-400/20 text-emerald-200'
              : 'bg-red-400/20 text-red-200'
          }`}
        >
          {isSuccess ? (
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
              <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm.75-11.25a.75.75 0 00-1.5 0v4.5a.75.75 0 001.5 0v-4.5zM10 14a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
            </svg>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold">
            {isSuccess ? 'Message Sent' : 'Message Failed'}
          </p>
          <p className="text-sm mt-1 text-white/75 leading-relaxed">
            {status.message}
          </p>
        </div>

        <button
          type="button"
          onClick={onClose}
          className="text-white/50 hover:text-white transition-colors"
          title="Close"
        >
          ✕
        </button>
      </div>
    </motion.div>
  );
};

const ContactUsPage = () => {
  const { instance, accounts } = useMsal();
  const navigate = useNavigate();
  const { theme } = useTheme();

  const user = accounts[0] || {};
  const autoName = user.name || '';
  const autoEmail = user.username || '';

  const [form, setForm] = useState({
    title: '',
    message: '',
  });

  const [status, setStatus] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
  if (!status) return;

  const timer = setTimeout(() => {
    setStatus(null);
  }, 4000);

  return () => clearTimeout(timer);
}, [status]);

  useEffect(() => {
    const root = document.documentElement;

    if (theme === 'dark') root.classList.add('dark');
    else root.classList.remove('dark');

    return () => {
      root.classList.remove('dark');
    };
  }, [theme]);

  const canSubmit = useMemo(() => {
    return autoName && autoEmail && form.title.trim() && form.message.trim() && !submitting;
  }, [autoName, autoEmail, form.title, form.message, submitting]);

  const handleLogin = () => {
    sessionStorage.setItem('intentionalLogin', 'true');
    sessionStorage.setItem('lastAgent', '/contact-us');
    instance.loginRedirect(loginRequest).catch((e) => console.error(e));
  };

  const handleLogout = () => {
    instance
      .logoutRedirect({
        postLogoutRedirectUri: window.location.origin + '/workmateai',
      })
      .catch((e) => console.error(e));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setStatus(null);

    try {
      const res = await fetch(`${API_BASE}/contact-us`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: autoName,
          email: autoEmail,
          title: form.title.trim(),
          message: form.message.trim(),
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || data.message || `HTTP ${res.status}`);
      }

      setStatus({
        type: 'success',
        message: data.message || 'Your message has been sent successfully.',
      });

      setForm({
        title: '',
        message: '',
      });
    } catch (err) {
      setStatus({
        type: 'error',
        message: err.message || 'Failed to send message.',
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="h-screen flex flex-row relative overflow-hidden bg-[#fbfbfd] dark:bg-[#14171c] text-gray-900 dark:text-gray-100">
      <AuthenticatedTemplate>
        <SidebarRail
          user={user}
          onLogout={handleLogout}
          onBackToChat={() => navigate('/workmateai')}
        />
      </AuthenticatedTemplate>

      <div className="flex-1 flex flex-col min-h-0 min-w-0 relative">
        <motion.header
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
          className="relative z-20 flex items-center gap-4 px-4 sm:px-14 py-3 sm:py-4"
        >
          <div className="min-w-0 flex-1 flex flex-col">
            <h1 className="text-lg sm:text-xl font-bold text-gray-950 dark:text-gray-100 tracking-tight leading-tight truncate">
              Contact Us
            </h1>
            <p className="hidden sm:block text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Send questions, issues, or feedback to the support team.
            </p>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            <AuthenticatedTemplate>
              <button
                type="button"
                onClick={() => navigate('/workmateai')}
                className="sm:hidden flex items-center justify-center w-9 h-9 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-300 shrink-0"
                title="Back to Workmate AI"
              >
                ←
              </button>
            </AuthenticatedTemplate>

            <img
              src={sltLogo}
              alt="SLTMobitel"
              className="h-7 sm:h-10 w-auto drop-shadow-sm"
            />
          </div>
        </motion.header>

        <main className="flex-1 flex items-center justify-center px-4 sm:px-6 pb-20 pt-4 relative z-10">
          <AuthenticatedTemplate>
            <motion.div
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
              className="w-full max-w-3xl"
            >
              <div className="text-center mb-8">
                <h2 className="text-3xl sm:text-5xl font-semibold text-gray-900 dark:text-gray-100 tracking-tight">
                  How can we help?
                </h2>
              </div>

              <div className="relative">
                <div className={`absolute -inset-3 rounded-[2.5rem] bg-gradient-to-r ${WORKMATE_COLOR} opacity-[0.10] dark:opacity-20 blur-2xl pointer-events-none`} />

                <div className="relative bg-white dark:bg-[#23262c] rounded-[2rem] border border-gray-200 dark:border-[#3a3f48] shadow-[0_20px_50px_-15px_rgba(0,0,0,0.12)] dark:shadow-[0_20px_50px_-15px_rgba(0,0,0,0.65)] p-5 sm:p-7">
                  <form onSubmit={handleSubmit} className="space-y-5">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div>
                        <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                          Name
                        </label>
                        <input
                          value={autoName}
                          readOnly
                          className="w-full rounded-2xl border border-gray-200 dark:border-[#3a3f48] bg-gray-50 dark:bg-[#1c1f24] px-4 py-3 text-sm text-gray-700 dark:text-gray-300 outline-none"
                        />
                      </div>

                      <div>
                        <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                          Email
                        </label>
                        <input
                          value={autoEmail}
                          readOnly
                          className="w-full rounded-2xl border border-gray-200 dark:border-[#3a3f48] bg-gray-50 dark:bg-[#1c1f24] px-4 py-3 text-sm text-gray-700 dark:text-gray-300 outline-none"
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                        Title
                      </label>
                      <input
                        value={form.title}
                        onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
                        placeholder="Enter message title"
                        className="w-full rounded-2xl border border-gray-200 dark:border-[#3a3f48] bg-white dark:bg-[#2a2e36] px-4 py-3 text-sm text-gray-900 dark:text-gray-100 placeholder:text-gray-400 dark:placeholder:text-gray-500 outline-none focus:border-cyan-400/70 dark:focus:border-cyan-400/70 transition-colors"
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                        Message
                      </label>
                      <textarea
                        value={form.message}
                        onChange={(e) => setForm((prev) => ({ ...prev, message: e.target.value }))}
                        placeholder="Write your message"
                        rows={6}
                        className="w-full rounded-2xl border border-gray-200 dark:border-[#3a3f48] bg-white dark:bg-[#2a2e36] px-4 py-3 text-sm text-gray-900 dark:text-gray-100 placeholder:text-gray-400 dark:placeholder:text-gray-500 outline-none focus:border-cyan-400/70 dark:focus:border-cyan-400/70 resize-none transition-colors"
                      />
                    </div>

                    <button
                        type="submit"
                        disabled={!canSubmit}
                        className="w-full rounded-full bg-[#087f9f] text-white py-3.5 text-sm font-semibold shadow-[0_0_0_1px_rgba(6,182,212,0.18),0_12px_35px_-14px_rgba(8,127,159,0.9)] hover:bg-[#0a8fb3] hover:shadow-[0_0_0_1px_rgba(6,182,212,0.25),0_16px_42px_-14px_rgba(8,127,159,1)] disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                        >
                        {submitting ? 'Sending...' : 'Send Message'}
                    </button>
                  </form>
                </div>
              </div>
            </motion.div>
          </AuthenticatedTemplate>

          <UnauthenticatedTemplate>
            <motion.div
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
              className="w-full max-w-md"
            >
              <div className="relative">
                <div className={`absolute -inset-3 rounded-[2.5rem] bg-gradient-to-r ${WORKMATE_COLOR} opacity-[0.10] dark:opacity-20 blur-2xl pointer-events-none`} />

                <div className="relative bg-white dark:bg-[#23262c] rounded-[2rem] border border-gray-200 dark:border-[#3a3f48] shadow-[0_20px_50px_-15px_rgba(0,0,0,0.12)] dark:shadow-[0_20px_50px_-15px_rgba(0,0,0,0.65)] p-8 text-center">
                  <h1 className="text-2xl font-bold text-gray-950 dark:text-gray-100 mb-3">
                    Login Required
                  </h1>

                  <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                    Please login with Microsoft to send a contact message.
                  </p>

                  <button
                    type="button"
                    onClick={handleLogin}
                    className="w-full rounded-full bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 py-3.5 text-sm font-semibold hover:bg-gray-800 dark:hover:bg-white transition-colors flex items-center justify-center gap-3"
                  >
                    <MicrosoftIcon />
                    Login with Microsoft
                  </button>
                </div>
              </div>
            </motion.div>
          </UnauthenticatedTemplate>
        </main>

        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center justify-center gap-1.5 pointer-events-auto cursor-default select-none z-20">
          <span className="text-[0.65rem] uppercase tracking-wider font-semibold text-gray-500 dark:text-gray-400">
            Powered by
          </span>
          <img
            src={embryoLogo}
            alt="Embryo Logo"
            className="h-[20px] w-auto object-contain dark:brightness-110"
          />
        </div>
      </div>

      <AnimatePresence>
        {status && (
            <StatusToast
            status={status}
            onClose={() => setStatus(null)}
            />
        )}
    </AnimatePresence>
    </div>
  );
};

export default ContactUsPage;