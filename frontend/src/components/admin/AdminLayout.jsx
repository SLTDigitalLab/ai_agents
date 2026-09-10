import React from 'react';
import { Link } from 'react-router-dom';
import sltLogo from '../../assets/slt-mobitel-logo.png';
import embryoLogo from '../../assets/embryo-logo.png';
import { useMsal } from '@azure/msal-react';

const AdminLayout = ({
    children,
    title,
    subtitle,
    backTo,
    backLabel = 'Back',
    backgroundVariant = 'default',
}) => {

    const isLegacyDark = backgroundVariant === 'legacy-dark';

    const pageClass = isLegacyDark
        ? 'min-h-screen bg-gradient-to-br from-[#090b1a] via-[#0f172a] to-[#071827] text-white relative overflow-hidden'
        : 'min-h-screen bg-[#111827] text-white relative overflow-hidden';

    return (
        <div className={pageClass}>
            {/* Page background */}
            <div className="fixed inset-0 pointer-events-none">
                {isLegacyDark ? (
                    <>
                        <div className="absolute inset-0 bg-gradient-to-br from-[#090b1a] via-[#0f172a] to-[#071827]" />
                        <div className="absolute top-[-18%] left-[-10%] w-[520px] h-[520px] rounded-full bg-purple-500/[0.08] blur-3xl" />
                        <div className="absolute bottom-[-12%] right-[-8%] w-[620px] h-[620px] rounded-full bg-cyan-500/[0.06] blur-3xl" />
                    </>
                ) : (
                    <>
                        <div className="absolute inset-0 bg-[#111827]" />
                        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.08),transparent_32%),radial-gradient(circle_at_bottom_right,rgba(255,255,255,0.05),transparent_34%)]" />
                    </>
                )}
            </div>

            {/* Header */}
            <header className="relative z-50 px-5 sm:px-8 pt-5">
                <div className="h-[72px] flex items-center justify-between">
                    <h1 className="text-white text-xl sm:text-2xl font-extrabold tracking-tight">
                        Workmate AI
                    </h1>

                    <img
                        src={sltLogo}
                        alt="SLT-MOBITEL"
                        className="h-10 sm:h-12 w-auto object-contain"
                    />
                </div>
            </header>

            {/* Main Content */}
            <main className="relative z-10 px-4 sm:px-6 lg:px-8 pt-7 pb-9">
                <div className="max-w-7xl mx-auto">
                    {(title || subtitle) && (
                        <section className="mb-9">
                            {backTo && (
                                <div className="mb-5 flex justify-start">
                                    <Link
                                        to={backTo}
                                        className="inline-flex items-center gap-2 text-sm font-bold text-white/90 transition-all hover:text-white hover:underline"
                                    >
                                        <svg
                                            xmlns="http://www.w3.org/2000/svg"
                                            fill="none"
                                            viewBox="0 0 24 24"
                                            strokeWidth={2}
                                            stroke="currentColor"
                                            className="h-4 w-4"
                                        >
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
                                        </svg>
                                        {backLabel}
                                    </Link>
                                </div>
                            )}

                            <div className="text-center">
                                {title && (
                                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-white drop-shadow-sm">
                                        {title}
                                    </h1>
                                )}

                                {subtitle && (
                                    <p className="text-white/70 text-sm sm:text-base mt-3 max-w-3xl mx-auto">
                                        {subtitle}
                                    </p>
                                )}
                            </div>
                        </section>
                    )}
                    {children}
                </div>
            </main>

            {/* Footer */}
            <footer className="relative z-10 px-5 sm:px-8 pb-7 mt-10">
                <div className="flex items-center justify-center gap-3">
                    <span className="text-[11px] sm:text-xs font-extrabold tracking-[0.18em] text-white/45 uppercase">
                        POWERED BY
                    </span>

                    <img
                        src={embryoLogo}
                        alt="The Embryo Innovation Centre"
                        className="h-7 w-auto object-contain opacity-90"
                    />
                </div>
            </footer>
        </div>
    );
};

export default AdminLayout;