import React, { useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import TranscriptEntry from './TranscriptEntry';

const TranscriptPanel = ({ transcript, onClear }) => {
    const transcriptEndRef = useRef(null);

    useEffect(() => {
        transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [transcript]);

    return (
        <motion.div
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            className="mt-8 w-full max-w-lg rounded-2xl border border-white/[0.07] bg-white/[0.025] overflow-hidden flex flex-col"
            style={{ maxHeight: 200 }}
        >
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.05] shrink-0">
                <span className="text-[0.58rem] uppercase tracking-[0.16em] font-bold text-gray-600">
                    Transcript
                </span>
                <button
                    onClick={onClear}
                    className="text-[0.65rem] text-gray-600 hover:text-gray-400 transition-colors"
                >
                    Clear
                </button>
            </div>
            <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
                {transcript.map((entry, i) => (
                    <TranscriptEntry key={i} entry={entry} />
                ))}
                <div ref={transcriptEndRef} />
            </div>
        </motion.div>
    );
};

export default TranscriptPanel;