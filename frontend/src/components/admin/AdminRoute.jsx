import React from 'react';
import { useMsal, AuthenticatedTemplate, UnauthenticatedTemplate } from '@azure/msal-react';
import { loginRequest } from '../../authConfig';
import { Navigate, Outlet } from 'react-router-dom';
import { motion } from 'framer-motion';

const AdminRoute = () => {
    const { instance, accounts } = useMsal();

    // Read allowed emails from .env, fallback to a dummy if missing
    // e.g. VITE_ADMIN_EMAILS="admin1@slt.com.lk,admin2@slt.com.lk"
    const allowedEmailsStr = import.meta.env.VITE_ADMIN_EMAILS || 'admin@slt.com.lk';
    const allowedEmails = allowedEmailsStr.split(',').map(e => e.trim().toLowerCase());

    const user = accounts[0] || null;
    const isAuthorized = user && allowedEmails.includes(user.username.toLowerCase());

    const handleLogin = () => {
        // Set flags so App.jsx knows where to redirect after MSAL returns
        sessionStorage.setItem('intentionalLogin', 'true');
        sessionStorage.setItem('lastAgent', window.location.pathname);

        instance.loginRedirect(loginRequest).catch(e => console.error(e));
    };

    return (
        <div className="min-h-screen bg-slate-950 text-white font-sans selection:bg-cyan-500/30">
            {/* ── Not Logged In ─────────────────────────────────────────── */}
            <UnauthenticatedTemplate>
                <div className="flex flex-col items-center justify-center min-h-screen p-4 relative overflow-hidden">
                    <div className="absolute inset-0 pointer-events-none">
                        <div className="absolute top-[20%] right-[30%] w-[400px] h-[400px] rounded-full bg-cyan-500/[0.05] blur-3xl" />
                        <div className="absolute bottom-[20%] left-[30%] w-[300px] h-[300px] rounded-full bg-purple-500/[0.05] blur-3xl" />
                    </div>

                    <motion.div
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        className="relative z-10 bg-white/[0.03] border border-white/[0.08] p-8 sm:p-12 rounded-3xl max-w-md w-full text-center shadow-2xl backdrop-blur-sm"
                    >
                        <div className="w-16 h-16 bg-white/[0.05] border border-white/[0.1] rounded-2xl flex items-center justify-center mx-auto mb-6 text-2xl">
                            🔒
                        </div>
                        <h1 className="text-2xl font-bold mb-3 tracking-tight">Admin Authentication</h1>
                        <p className="text-white/50 text-sm mb-8">
                            Please sign in with your Microsoft account to access the Admin Panel.
                        </p>

                        <button
                            onClick={handleLogin}
                            className="w-full flex items-center justify-center gap-3 bg-white text-slate-900 hover:bg-white/90 font-semibold py-3 px-4 rounded-xl transition-all shadow-lg hover:shadow-cyan-500/20 active:scale-[0.98]"
                        >
                            <svg width="20" height="20" viewBox="0 0 21 21" fill="none">
                                <rect x="1" y="1" width="9" height="9" fill="#F25022" />
                                <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
                                <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
                                <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
                            </svg>
                            Sign in with Microsoft
                        </button>
                    </motion.div>
                </div>
            </UnauthenticatedTemplate>

            {/* ── Logged In but NOT Authorized ──────────────────────────── */}
            <AuthenticatedTemplate>
                {!isAuthorized ? (
                    <div className="flex flex-col items-center justify-center min-h-screen p-4 relative overflow-hidden">
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="bg-red-500/10 border border-red-500/20 p-8 sm:p-10 rounded-3xl max-w-md w-full text-center"
                        >
                            <div className="text-red-400 mb-4">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-16 h-16 mx-auto">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                                </svg>
                            </div>
                            <h2 className="text-2xl font-bold text-red-100 mb-2">Access Denied</h2>
                            <p className="text-red-200/70 text-sm mb-6 leading-relaxed">
                                Your account (<strong>{user?.username}</strong>) does not have permission to view the Admin Panel.
                                <br /><br />
                                If you need access, please contact the system administrator to whitelist your email address.
                            </p>

                            <div className="space-y-3">
                                <button
                                    onClick={() => instance.logoutRedirect()}
                                    className="w-full bg-red-500 hover:bg-red-600 text-white font-medium py-3 px-4 rounded-xl transition-colors"
                                >
                                    Sign Out & Try Another Account
                                </button>
                            </div>
                        </motion.div>
                    </div>
                ) : (
                    // ── Logged In AND Authorized (Render the requested admin page) ──
                    <Outlet />
                )}
            </AuthenticatedTemplate>
        </div>
    );
};

export default AdminRoute;
