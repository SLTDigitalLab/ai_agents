import React from 'react';
import { motion } from 'framer-motion';
import { PHASE } from './constants';

const CallControls = ({ phase, onStart, onEnd }) => {
    if (phase === PHASE.IDLE || phase === PHASE.ERROR) {
        return (
            <motion.button
                whileHover={{ scale: 1.03 }}
                whileTap={{ scale: 0.97 }}
                onClick={onStart}
                className="flex items-center gap-2.5 px-8 py-3.5 rounded-full bg-gradient-to-r from-cyan-900 to-cyan-600 text-white font-semibold text-sm shadow-lg shadow-cyan-900/20 hover:shadow-cyan-900/30 transition-shadow duration-300"
            >
                <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                    <path d="M8.25 4.5a3.75 3.75 0 117.5 0v8.25a3.75 3.75 0 11-7.5 0V4.5z" />
                    <path d="M6 10.5a.75.75 0 01.75.75v1.5a5.25 5.25 0 1010.5 0v-1.5a.75.75 0 011.5 0v1.5a6.751 6.751 0 01-6 6.709v2.291h3a.75.75 0 010 1.5h-7.5a.75.75 0 010-1.5h3v-2.291a6.751 6.751 0 01-6-6.709v-1.5A.75.75 0 016 10.5z" />
                </svg>
                {phase === PHASE.ERROR ? 'Try Again' : 'Start Conversation'}
            </motion.button>
        );
    }

    if (phase === PHASE.CONNECTING) {
        return (
            <div className="flex items-center gap-2.5 px-8 py-3.5 rounded-full bg-gray-100 dark:bg-white/[0.05] border border-gray-200 dark:border-white/[0.07] text-gray-500 text-sm cursor-default">
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2.5" />
                    <path className="opacity-60" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                Connecting...
            </div>
        );
    }

    return (
        <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={onEnd}
            className="flex items-center gap-2.5 px-8 py-3.5 rounded-full bg-red-500/[0.08] hover:bg-red-500/[0.15] border border-red-500/20 hover:border-red-500/30 text-red-400 hover:text-red-300 font-semibold text-sm transition-all duration-250"
        >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75 9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25zm-1.72 6.97a.75.75 0 10-1.06 1.06L10.94 12l-1.72 1.72a.75.75 0 101.06 1.06L12 13.06l1.72 1.72a.75.75 0 101.06-1.06L13.06 12l1.72-1.72a.75.75 0 10-1.06-1.06L12 10.94l-1.72-1.72z" clipRule="evenodd" />
            </svg>
            End Call
        </motion.button>
    );
};

export default CallControls;