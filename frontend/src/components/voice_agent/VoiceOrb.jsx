import React from 'react';
import { motion } from 'framer-motion';
import { PHASE } from './constants';

const VoiceOrb = ({ phase, isSpeaking, isListening, theme = 'dark' }) => {
    const isActive = phase === PHASE.CONNECTED;
    const isError = phase === PHASE.ERROR;
    const isDark = theme !== 'light';

    return (
        <div className="relative flex items-center justify-center" style={{ width: 220, height: 220 }}>

            {/* Outermost ambient glow — only when active */}
            {isActive && (
                <div
                    className="absolute inset-0 rounded-full"
                    style={{
                        background: isSpeaking
                            ? 'radial-gradient(circle, rgba(45,212,191,0.12) 0%, transparent 70%)'
                            : 'radial-gradient(circle, rgba(45,212,191,0.06) 0%, transparent 70%)',
                        transition: 'background 0.4s ease',
                    }}
                />
            )}

            {/* Ping rings */}
            {isActive && isSpeaking && (
                <>
                    <div className="absolute w-52 h-52 rounded-full border border-teal-400/15 animate-ping" style={{ animationDuration: '2.2s' }} />
                    <div className="absolute w-44 h-44 rounded-full border border-teal-400/20 animate-ping" style={{ animationDuration: '1.7s', animationDelay: '0.35s' }} />
                </>
            )}
            {isActive && isListening && !isSpeaking && (
                <div className="absolute w-44 h-44 rounded-full border border-cyan-400/25 animate-ping" style={{ animationDuration: '1.4s' }} />
            )}

            {/* Idle slow pulse ring */}
            {isActive && !isSpeaking && !isListening && (
                <motion.div
                    className="absolute w-40 h-40 rounded-full border border-teal-400/10"
                    animate={{ scale: [1, 1.06, 1], opacity: [0.4, 0.7, 0.4] }}
                    transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
                />
            )}

            {/* Core orb */}
            <motion.div
                animate={{
                    scale: isSpeaking
                        ? [1, 1.1, 1.05, 1.08, 1]
                        : isListening
                        ? [1, 1.04, 1]
                        : isActive
                        ? [1, 1.025, 1]
                        : 1,
                }}
                transition={{
                    duration: isSpeaking ? 0.55 : 2.5,
                    repeat: isActive ? Infinity : 0,
                    ease: 'easeInOut',
                }}
                className="relative flex items-center justify-center"
                style={{
                    width: 128,
                    height: 128,
                    borderRadius: '50%',
                    background: isError
                        ? 'radial-gradient(circle at 35% 35%, rgba(239,68,68,0.25), rgba(185,28,28,0.15))'
                        : isActive
                        ? isSpeaking
                            ? 'radial-gradient(circle at 35% 35%, rgba(45,212,191,0.3), rgba(6,182,212,0.15))'
                            : 'radial-gradient(circle at 35% 35%, rgba(45,212,191,0.18), rgba(6,182,212,0.08))'
                        : isDark
                        ? 'radial-gradient(circle at 35% 35%, rgba(255,255,255,0.06), rgba(255,255,255,0.02))'
                        : 'radial-gradient(circle at 35% 35%, rgba(0,0,0,0.06), rgba(0,0,0,0.02))',
                    border: isError
                        ? '1px solid rgba(239,68,68,0.25)'
                        : isActive
                        ? isSpeaking
                            ? '1px solid rgba(45,212,191,0.35)'
                            : '1px solid rgba(45,212,191,0.2)'
                        : isDark
                        ? '1px solid rgba(255,255,255,0.08)'
                        : '1px solid rgba(0,0,0,0.10)',
                    boxShadow: isActive
                        ? isSpeaking
                            ? '0 0 55px rgba(45,212,191,0.3), 0 0 110px rgba(45,212,191,0.1), inset 0 1px 0 rgba(255,255,255,0.08)'
                            : isListening
                            ? '0 0 45px rgba(6,182,212,0.28), inset 0 1px 0 rgba(255,255,255,0.06)'
                            : '0 0 30px rgba(45,212,191,0.14), inset 0 1px 0 rgba(255,255,255,0.06)'
                        : isDark
                        ? 'inset 0 1px 0 rgba(255,255,255,0.04)'
                        : '0 6px 20px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.6)',
                    transition: 'background 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease',
                }}
            >
                {/* Specular highlight */}
                <div
                    className="absolute"
                    style={{
                        width: 40, height: 40,
                        borderRadius: '50%',
                        top: 18, left: 22,
                        background: 'radial-gradient(circle, rgba(255,255,255,0.07) 0%, transparent 70%)',
                    }}
                />

                {phase === PHASE.CONNECTING ? (
                    <svg className="w-8 h-8 text-gray-500 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2.5" />
                        <path className="opacity-60" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                ) : isError ? (
                    <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8 text-red-400">
                        <path fillRule="evenodd" d="M9.401 3.003c1.155-2 4.043-2 5.197 0l7.355 12.748c1.154 2-.29 4.5-2.599 4.5H4.645c-2.309 0-3.752-2.5-2.598-4.5L9.4 3.003zM12 8.25a.75.75 0 01.75.75v3.75a.75.75 0 01-1.5 0V9a.75.75 0 01.75-.75zm0 8.25a.75.75 0 100-1.5.75.75 0 000 1.5z" clipRule="evenodd" />
                    </svg>
                ) : (
                    <svg viewBox="0 0 24 24" fill="currentColor" className={`w-8 h-8 transition-colors duration-300 ${
                        isActive ? (isSpeaking ? 'text-teal-300' : isListening ? 'text-cyan-300' : 'text-gray-400') : 'text-gray-400 dark:text-gray-600'
                    }`}>
                        <path d="M8.25 4.5a3.75 3.75 0 117.5 0v8.25a3.75 3.75 0 11-7.5 0V4.5z" />
                        <path d="M6 10.5a.75.75 0 01.75.75v1.5a5.25 5.25 0 1010.5 0v-1.5a.75.75 0 011.5 0v1.5a6.751 6.751 0 01-6 6.709v2.291h3a.75.75 0 010 1.5h-7.5a.75.75 0 010-1.5h3v-2.291a6.751 6.751 0 01-6-6.709v-1.5A.75.75 0 016 10.5z" />
                    </svg>
                )}
            </motion.div>
        </div>
    );
};

export default VoiceOrb;