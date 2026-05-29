import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';

const ThemeContext = createContext({ theme: 'light', setTheme: () => {}, toggleTheme: () => {} });

const STORAGE_KEY = 'workmate.theme';

const getInitialTheme = () => {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored === 'light' || stored === 'dark') return stored;
    } catch {}
    if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches) {
        return 'dark';
    }
    return 'light';
};

export const ThemeProvider = ({ children }) => {
    const [theme, setThemeState] = useState(getInitialTheme);

    const setTheme = useCallback((next) => {
        setThemeState(next);
        try { localStorage.setItem(STORAGE_KEY, next); } catch {}
    }, []);

    const toggleTheme = useCallback(() => {
        setTheme(theme === 'dark' ? 'light' : 'dark');
    }, [theme, setTheme]);

    // Follow system changes only when user hasn't made an explicit choice.
    useEffect(() => {
        try {
            if (localStorage.getItem(STORAGE_KEY)) return;
        } catch { return; }
        const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
        if (!mq) return;
        const handler = (e) => setThemeState(e.matches ? 'dark' : 'light');
        mq.addEventListener?.('change', handler);
        return () => mq.removeEventListener?.('change', handler);
    }, []);

    return (
        <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>
            {children}
        </ThemeContext.Provider>
    );
};

export const useTheme = () => useContext(ThemeContext);
