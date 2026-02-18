/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
            },
            animation: {
                'breathing': 'breathing 8s ease-in-out infinite',
                'float-slow': 'floatBlob 12s ease-in-out infinite',
                'float-slower': 'floatBlob 16s ease-in-out infinite alternate',
                'glow-sweep': 'glowSweep 2s ease-in-out infinite',
                'pulse-glow': 'pulseGlow 3s ease-in-out infinite',
            },
            keyframes: {
                breathing: {
                    '0%, 100%': { backgroundPosition: '0% 50%' },
                    '50%': { backgroundPosition: '100% 50%' },
                },
                floatBlob: {
                    '0%': { transform: 'translate(0, 0) scale(1)' },
                    '33%': { transform: 'translate(30px, -40px) scale(1.05)' },
                    '66%': { transform: 'translate(-20px, 20px) scale(0.95)' },
                    '100%': { transform: 'translate(0, 0) scale(1)' },
                },
                glowSweep: {
                    '0%': { left: '-100%' },
                    '50%, 100%': { left: '100%' },
                },
                pulseGlow: {
                    '0%, 100%': { opacity: '0.4' },
                    '50%': { opacity: '0.8' },
                },
            },
        },
    },
    plugins: [],
}