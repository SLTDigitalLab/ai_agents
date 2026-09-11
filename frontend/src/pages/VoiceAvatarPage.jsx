import { useState } from 'react';
import { motion as Motion } from 'framer-motion';
import sltLogo from '../assets/slt-mobitel-logo.png';
import embryoLogo from '../assets/embryo-removebg.png';
import VoiceAgentPage from './VoiceAgentPage';
import SimliAvatarPanel from '../components/SimliAvatarPanel';
import { createSimliAudioOutput } from '../components/avatar/SimliAudioOutput';
import UserMenu from '../components/voice_agent/UserMenu';
import { PHASE } from '../components/voice_agent/constants';

export default function VoiceAvatarPage() {
    const [audioOutput] = useState(() => createSimliAudioOutput());
    return (
        <VoiceAgentPage
            audioOutput={audioOutput}
            renderPresentation={({
                phase, startConversation, errorMessage, displayStatus,
                user, initials, showUserMenu, setShowUserMenu, handleLogout,
                theme, toggleTheme, backToChat,
            }) => (
                <div className="visual-agent-shell">
            <div className="hidden sm:flex w-14 flex-shrink-0 flex-col items-center py-4 gap-2 border-r border-gray-200 dark:border-white/[0.06] bg-white dark:bg-[#0d0f14]">
                <div className="flex-1" />

                {/* Theme toggle */}
                <button
                    onClick={toggleTheme}
                    title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                    className="w-9 h-9 flex items-center justify-center rounded-xl text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/[0.07] transition-all duration-200"
                >
                    {theme === 'dark' ? (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                            <path d="M10 2a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 2zM10 15a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 15zM10 7a3 3 0 100 6 3 3 0 000-6zM15.657 5.404a.75.75 0 10-1.06-1.06l-1.061 1.06a.75.75 0 001.06 1.06l1.06-1.06zM6.464 14.596a.75.75 0 10-1.06-1.06l-1.06 1.06a.75.75 0 001.06 1.06l1.06-1.06zM18 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 0118 10zM5 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 015 10zM14.596 15.657a.75.75 0 001.06-1.06l-1.06-1.061a.75.75 0 10-1.06 1.06l1.06 1.061zM5.404 6.464a.75.75 0 001.06-1.06l-1.06-1.06a.75.75 0 10-1.06 1.06l1.06 1.06z" />
                        </svg>
                    ) : (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
                            <path fillRule="evenodd" d="M7.455 2.004a.75.75 0 01.26.77 7 7 0 009.958 7.967.75.75 0 011.067.853A8.5 8.5 0 116.647 1.921a.75.75 0 01.808.083z" clipRule="evenodd" />
                        </svg>
                    )}
                </button>

                <UserMenu
                    user={user}
                    initials={initials}
                    showMenu={showUserMenu}
                    onToggle={setShowUserMenu}
                    onLogout={handleLogout}
                />
            </div>

                <main className="workmate-visual-page" aria-label="Workmate voice avatar">
                    <header className="avatar-page-header">
                    <Motion.button
                        type="button"
                        onClick={backToChat}
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        title="Back to Chat Agent"
                        className="group flex items-center gap-2 pl-3 pr-4 sm:pl-4 sm:pr-5 py-2 rounded-full bg-gradient-to-r from-cyan-900 to-cyan-600 text-white text-sm font-semibold shadow-md hover:shadow-lg ring-1 ring-black/5 transition-all shrink-0"
                    >
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            fill="none"
                            viewBox="0 0 24 24"
                            strokeWidth={2.2}
                            stroke="currentColor"
                            className="w-4 h-4 transition-transform duration-300 ease-out group-hover:-translate-x-1"
                        >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
                        </svg>
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                            <path fillRule="evenodd" d="M4.848 2.771A49.144 49.144 0 0112 2.25c2.43 0 4.817.178 7.152.52 1.978.292 3.348 2.024 3.348 3.97v6.02c0 1.946-1.37 3.678-3.348 3.97a48.901 48.901 0 01-3.476.383.39.39 0 00-.297.17l-2.755 4.133a.75.75 0 01-1.248 0l-2.755-4.133a.39.39 0 00-.297-.17 48.9 48.9 0 01-3.476-.384c-1.978-.29-3.348-2.024-3.348-3.97V6.741c0-1.946 1.37-3.68 3.348-3.97z" clipRule="evenodd" />
                        </svg>
                        <span className="hidden sm:inline">Chat Agent</span>
                    </Motion.button>


                        <h1>Visual Agent</h1>
                        <img src={sltLogo} alt="SLTMobitel" />
                    </header>
                    <SimliAvatarPanel audioOutput={audioOutput} showControls={false} />
                    <div className="avatar-conversation-controls">
                        <div className="visual-conversation-status" role="status" aria-live="polite">
                            <span className={phase === PHASE.CONNECTING ? 'visual-status-dot connecting' : 'visual-status-dot'} aria-hidden="true" />
                            {displayStatus}
                        </div>
                        {errorMessage && <p role="status">{errorMessage}</p>}
                        <Motion.button
                            whileHover={{ scale: 1.03 }}
                            whileTap={{ scale: 0.97 }}
                            type="button"
                            onClick={startConversation}
                            disabled={phase === PHASE.CONNECTING || phase === PHASE.CONNECTED}
                            aria-busy={phase === PHASE.CONNECTING}
                        >
                            {phase === PHASE.CONNECTING ? 'Connecting...'
                                : phase === PHASE.CONNECTED ? 'Conversation started'
                                : 'Start Conversation'}
                        </Motion.button>
                        <div className="avatar-page-attribution">
                            <span>WorkMate AI powered by VoiceGenie AI</span>
                            <img src={embryoLogo} alt="Embryo Logo" />
                        </div>
                    </div>
                </main>
                </div>
            )}
        />
    );
}
