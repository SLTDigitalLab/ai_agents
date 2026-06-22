import React from 'react';
import { motion } from 'framer-motion';

const TranscriptEntry = ({ entry }) => (
    <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
        className={`flex ${entry.role === 'user' ? 'justify-end' : 'justify-start'}`}
    >
        <div className={`max-w-[82%] px-4 py-2.5 rounded-2xl text-[0.82rem] leading-relaxed ${
            entry.role === 'user'
                ? 'bg-gradient-to-br from-teal-500 to-cyan-600 text-white rounded-tr-sm'
                : 'text-gray-300 rounded-tl-sm'
        }`}>
            {entry.role !== 'user' && (
                <span className="text-[0.6rem] uppercase tracking-widest font-bold block mb-1 text-teal-400/70">
                    Workmate AI
                </span>
            )}
            {entry.text}
        </div>
    </motion.div>
);

export default TranscriptEntry;