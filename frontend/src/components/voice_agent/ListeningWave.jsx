import React from 'react';
import { motion } from 'framer-motion';

const HEIGHTS = [3, 6, 10, 7, 12, 8, 5, 9, 6, 4, 8, 5];

const ListeningWave = ({ isListening, isSpeaking }) => {
    const active = isListening || isSpeaking;

    return (
        <div className="flex items-center justify-center gap-[3px] h-5">
            {HEIGHTS.map((h, i) => (
                <motion.div
                    key={i}
                    className="w-[3px] rounded-full"
                    style={{ backgroundColor: isSpeaking ? 'rgba(94,234,212,0.7)' : 'rgba(34,211,238,0.6)' }}
                    animate={active ? { height: [h * 0.4, h, h * 0.6, h * 0.9, h * 0.4] } : { height: 2 }}
                    transition={active ? {
                        duration: 0.7 + i * 0.05,
                        repeat: Infinity,
                        ease: 'easeInOut',
                        delay: i * 0.06,
                    } : { duration: 0.3 }}
                />
            ))}
        </div>
    );
};

export default ListeningWave;