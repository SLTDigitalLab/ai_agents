import React, { useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

const UserMenu = ({ user, initials, showMenu, onToggle, onLogout }) => {
    useEffect(() => {
        if (!showMenu) return;
        const handler = () => onToggle(false);
        document.addEventListener('click', handler);
        return () => document.removeEventListener('click', handler);
    }, [showMenu, onToggle]);

    return (
        <div className="relative">
            <button
                onClick={(e) => { e.stopPropagation(); onToggle(prev => !prev); }}
                title={user.name}
                className="w-9 h-9 rounded-full bg-gradient-to-br from-teal-500 to-cyan-600 flex items-center justify-center text-white text-[0.65rem] font-bold hover:opacity-85 transition-opacity ring-2 ring-transparent hover:ring-teal-500/30"
            >
                {initials}
            </button>

            <AnimatePresence>
                {showMenu && (
                    <motion.div
                        initial={{ opacity: 0, x: -6, scale: 0.96 }}
                        animate={{ opacity: 1, x: 0, scale: 1 }}
                        exit={{ opacity: 0, x: -6, scale: 0.96 }}
                        transition={{ duration: 0.14 }}
                        onClick={e => e.stopPropagation()}
                        className="absolute bottom-0 left-12 w-52 bg-[#1a1d24] border border-white/[0.08] rounded-2xl shadow-2xl p-3 z-50"
                    >
                        <div className="px-2 py-1.5 mb-2 border-b border-white/[0.06]">
                            <p className="text-[0.82rem] font-semibold text-gray-100 truncate">{user.name}</p>
                            <p className="text-xs text-gray-500 truncate mt-0.5">{user.username}</p>
                        </div>
                        <button
                            onClick={onLogout}
                            className="w-full flex items-center gap-2.5 px-2 py-2 rounded-xl text-[0.82rem] text-gray-400 hover:text-gray-100 hover:bg-white/[0.06] transition-all duration-150"
                        >
                            <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 shrink-0">
                                <path fillRule="evenodd" d="M3 4.25A2.25 2.25 0 015.25 2h5.5A2.25 2.25 0 0113 4.25v2a.75.75 0 01-1.5 0v-2a.75.75 0 00-.75-.75h-5.5a.75.75 0 00-.75.75v11.5c0 .414.336.75.75.75h5.5a.75.75 0 00.75-.75v-2a.75.75 0 011.5 0v2A2.25 2.25 0 0110.75 18h-5.5A2.25 2.25 0 013 15.75V4.25z" clipRule="evenodd" />
                                <path fillRule="evenodd" d="M6 10a.75.75 0 01.75-.75h9.546l-1.048-.943a.75.75 0 111.004-1.114l2.5 2.25a.75.75 0 010 1.114l-2.5 2.25a.75.75 0 11-1.004-1.114l1.048-.943H6.75A.75.75 0 016 10z" clipRule="evenodd" />
                            </svg>
                            Logout
                        </button>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};

export default UserMenu;