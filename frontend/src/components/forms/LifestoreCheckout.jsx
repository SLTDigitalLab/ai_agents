import React, { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { useLifestoreAuth } from '../../auth/lifestoreAuth';

// Split a Google display name into first / last for the receipt fields.
const splitName = (fullName = '') => {
    const parts = fullName.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return { first: '', last: '' };
    if (parts.length === 1) return { first: parts[0], last: '' };
    return { first: parts[0], last: parts.slice(1).join(' ') };
};

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const PAYHERE_SDK = 'https://www.payhere.lk/lib/payhere.js';

// Load the PayHere onsite-checkout SDK once, shared across all checkout cards.
let payherePromise = null;
const loadPayHere = () => {
    if (window.payhere) return Promise.resolve(window.payhere);
    if (payherePromise) return payherePromise;

    payherePromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = PAYHERE_SDK;
        script.async = true;
        script.onload = () => resolve(window.payhere);
        script.onerror = () => reject(new Error('Failed to load PayHere SDK'));
        document.body.appendChild(script);
    });
    return payherePromise;
};

/**
 * Chat-embedded PayHere checkout for Ask LifeStore.
 *
 * Everything financial (amount, order id, currency, hash) is fetched from the
 * backend by orderId — the chat/LLM never carries it. Payment is confirmed only
 * by the backend (webhook, or the reconcile fallback), never by this component.
 */
const LifestoreCheckout = ({ orderId }) => {
    const [checkout, setCheckout] = useState(null);
    const [loadError, setLoadError] = useState('');
    // 'idle' | 'paying' | 'processing' | 'paid' | 'failed' | 'canceled'
    const [status, setStatus] = useState('idle');
    const [customer, setCustomer] = useState({
        first_name: '',
        last_name: '',
        email: '',
        phone: '',
    });
    const [customerError, setCustomerError] = useState('');

    const { user } = useLifestoreAuth();
    const prefilledRef = useRef(false);

    const pollRef = useRef(null);

    // ── Prefill name + email from the signed-in Google account ────────────
    // Runs once when the user becomes available, and only fills fields the
    // customer hasn't already typed into — so their edits are never clobbered.
    // Phone isn't part of the Google profile, so it stays empty for them.
    useEffect(() => {
        if (prefilledRef.current || !user) return;
        prefilledRef.current = true;

        const { first, last } = splitName(user.name);
        setCustomer((prev) => ({
            ...prev,
            first_name: prev.first_name || first,
            last_name: prev.last_name || last,
            email: prev.email || user.email || '',
        }));
    }, [user]);

    // ── Fetch the authoritative checkout payload ──────────────────────────
    useEffect(() => {
        if (!orderId) return;
        let cancelled = false;

        (async () => {
            try {
                const res = await fetch(`${API_URL}/api/v1/lifestore/checkout/${orderId}`);
                if (!res.ok) throw new Error(`Checkout unavailable (${res.status})`);
                const data = await res.json();
                if (cancelled) return;
                setCheckout(data);
                if (data.status && data.status !== 'PENDING') {
                    setStatus(data.status === 'PAID' ? 'paid'
                        : data.status === 'CANCELED' ? 'canceled' : 'failed');
                }
            } catch (err) {
                if (!cancelled) setLoadError(err.message || 'Could not load checkout.');
            }
        })();

        return () => {
            cancelled = true;
            if (pollRef.current) clearInterval(pollRef.current);
        };
    }, [orderId]);

    // ── Poll the backend for the settled status (source of truth) ─────────
    const startPolling = () => {
        if (pollRef.current) clearInterval(pollRef.current);
        let attempts = 0;

        // Ask the backend to reconcile with PayHere directly (helps when the
        // webhook can't reach a local dev server). Harmless in production.
        fetch(`${API_URL}/api/v1/lifestore/orders/${orderId}/reconcile`, { method: 'POST' })
            .catch(() => {});

        pollRef.current = setInterval(async () => {
            attempts += 1;
            try {
                const res = await fetch(`${API_URL}/api/v1/lifestore/orders/${orderId}`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.status === 'PAID') { finish('paid'); return; }
                    if (data.status === 'FAILED') { finish('failed'); return; }
                    if (data.status === 'CANCELED') { finish('canceled'); return; }
                }
            } catch (_) { /* keep polling */ }

            if (attempts >= 20) {
                clearInterval(pollRef.current);
                // Still pending after ~1 min — leave in processing; webhook may
                // arrive later. The customer is told confirmation is pending.
            }
        }, 3000);
    };

    const finish = (next) => {
        if (pollRef.current) clearInterval(pollRef.current);
        setStatus(next);
    };

    const isValidEmail = (value) => /\S+@\S+\.\S+/.test(value.trim());

    const handlePay = async () => {
        if (!checkout?.payhere) return;
        setLoadError('');

        if (
            !customer.first_name.trim() ||
            !customer.last_name.trim() ||
            !isValidEmail(customer.email) ||
            !customer.phone.trim()
        ) {
            setCustomerError('Please fill in your name, a valid email, and phone number before paying.');
            return;
        }
        setCustomerError('');

        let payhere;
        try {
            payhere = await loadPayHere();
        } catch (err) {
            setLoadError('Could not load the PayHere payment SDK.');
            return;
        }

        payhere.onCompleted = () => {
            // NOTE: onCompleted only means the flow finished, NOT that payment
            // succeeded. The backend confirms the real status.
            setStatus('processing');
            startPolling();
        };
        payhere.onDismissed = () => {
            if (status !== 'paid') setStatus('idle');
        };
        payhere.onError = (msg) => {
            setLoadError(typeof msg === 'string' ? msg : 'Payment error.');
            setStatus('idle');
        };

        const p = checkout.payhere;
        setStatus('paying');
        payhere.startPayment({
            sandbox: p.sandbox,
            merchant_id: p.merchant_id,
            return_url: p.return_url,
            cancel_url: p.cancel_url,
            notify_url: p.notify_url,
            order_id: p.order_id,
            items: p.items,
            amount: p.amount,
            currency: p.currency,
            hash: p.hash,
            first_name: customer.first_name,
            last_name: customer.last_name,
            email: customer.email,
            phone: customer.phone,
            address: 'N/A',
            city: 'Colombo',
            country: 'Sri Lanka',
        });
    };

    const onCustomer = (e) =>
        setCustomer((prev) => ({ ...prev, [e.target.name]: e.target.value }));

    // ── Render states ─────────────────────────────────────────────────────
    if (!orderId) return null;

    if (loadError && !checkout) {
        return (
            <div className="w-full my-3 bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
                {loadError}
            </div>
        );
    }

    if (!checkout) {
        return (
            <div className="w-full my-3 bg-white border border-gray-200 rounded-2xl p-5 text-sm text-gray-500 animate-pulse">
                Preparing secure checkout…
            </div>
        );
    }

    if (status === 'paid') {
        return (
            <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} className="w-full my-3">
                <div className="bg-gradient-to-br from-green-50 to-emerald-50 border border-green-200 rounded-2xl p-6">
                    <div className="flex flex-col items-center text-center mb-4">
                        <svg className="w-12 h-12 text-green-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                        </svg>
                        <h3 className="text-lg font-bold text-green-800">Payment successful</h3>
                        <p className="text-sm text-green-600 mt-1">
                            Order <span className="font-mono">{checkout.order_id}</span> is paid — {checkout.amount_display}.
                        </p>
                        <p className="text-xs text-green-500 mt-2">Sandbox demo — no real money was charged.</p>
                    </div>

                    {checkout.lines?.length > 0 && (
                        <div className="bg-white/70 divide-y divide-green-100 border border-green-200 rounded-lg text-left">
                            <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-green-700">
                                Order summary
                            </div>
                            {checkout.lines.map((line) => (
                                <div key={line.product_id} className="flex items-center justify-between px-3 py-2 text-sm">
                                    <span className="text-gray-700 pr-3">
                                        {line.name}<span className="text-gray-400"> × {line.quantity}</span>
                                        <span className="text-gray-400"> ({line.unit_price_display} each)</span>
                                    </span>
                                    <span className="text-gray-800 font-medium whitespace-nowrap">{line.line_total_display}</span>
                                </div>
                            ))}
                            <div className="flex items-center justify-between px-3 py-2 text-sm bg-green-50">
                                <span className="font-semibold text-gray-800">Total paid</span>
                                <span className="font-bold text-gray-900">{checkout.amount_display}</span>
                            </div>
                        </div>
                    )}
                </div>
            </motion.div>
        );
    }

    if (status === 'failed' || status === 'canceled') {
        return (
            <div className="w-full my-3 bg-orange-50 border border-orange-200 rounded-2xl p-5 text-center">
                <h3 className="text-base font-bold text-orange-800">
                    Payment {status === 'canceled' ? 'canceled' : 'not completed'}
                </h3>
                <p className="text-sm text-orange-600 mt-1">
                    No charge was made. You can try paying again.
                </p>
                <button
                    onClick={() => setStatus('idle')}
                    className="mt-3 text-sm font-medium text-white bg-orange-500 hover:bg-orange-600 rounded-md px-4 py-2"
                >
                    Try again
                </button>
            </div>
        );
    }

    return (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="w-full my-3">
            <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                <div className="flex items-center justify-between mb-1">
                    <h3 className="text-lg font-bold text-gray-800">Checkout</h3>
                    {checkout.is_demo && (
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 bg-amber-100 border border-amber-200 rounded-full px-2 py-0.5">
                            Sandbox demo
                        </span>
                    )}
                </div>
                <p className="text-xs text-gray-400 mb-4">
                    Secure payment via PayHere. This is a demo — no real money is charged.
                </p>

                <div className="divide-y divide-gray-100 border border-gray-100 rounded-lg mb-4">
                    {checkout.lines.map((line) => (
                        <div key={line.product_id} className="flex items-center justify-between px-3 py-2 text-sm">
                            <span className="text-gray-700 pr-3">
                                {line.name}<span className="text-gray-400"> × {line.quantity}</span>
                            </span>
                            <span className="text-gray-800 font-medium whitespace-nowrap">{line.line_total_display}</span>
                        </div>
                    ))}
                    <div className="flex items-center justify-between px-3 py-2 text-sm bg-gray-50">
                        <span className="font-semibold text-gray-800">Total</span>
                        <span className="font-bold text-gray-900">{checkout.amount_display}</span>
                    </div>
                </div>

                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                    Your details (for the payment receipt)
                </p>
                <div className="grid grid-cols-2 gap-3 mb-4">
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">
                            First name <span className="text-red-500">*</span>
                        </label>
                        <input name="first_name" value={customer.first_name} onChange={onCustomer}
                            placeholder="e.g., Saman"
                            className="w-full border border-gray-300 rounded-md p-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-orange-300" />
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">
                            Last name <span className="text-red-500">*</span>
                        </label>
                        <input name="last_name" value={customer.last_name} onChange={onCustomer}
                            placeholder="e.g., Perera"
                            className="w-full border border-gray-300 rounded-md p-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-orange-300" />
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">
                            Email <span className="text-red-500">*</span>
                        </label>
                        <input name="email" value={customer.email} onChange={onCustomer}
                            placeholder="e.g., saman@email.com" type="email"
                            className="w-full border border-gray-300 rounded-md p-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-orange-300" />
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">
                            Phone <span className="text-red-500">*</span>
                        </label>
                        <input name="phone" value={customer.phone} onChange={onCustomer}
                            placeholder="e.g., 0771234567"
                            className="w-full border border-gray-300 rounded-md p-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-orange-300" />
                    </div>
                </div>

                {customerError && (
                    <div className="mb-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">{customerError}</div>
                )}

                {loadError && (
                    <div className="mb-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">{loadError}</div>
                )}

                {status === 'processing' ? (
                    <div className="text-sm text-gray-600 bg-gray-50 border border-gray-200 rounded-md p-3 text-center">
                        Confirming payment… please wait.
                    </div>
                ) : (
                    <button
                        onClick={handlePay}
                        disabled={status === 'paying'}
                        className="w-full text-sm font-semibold text-white bg-orange-500 hover:bg-orange-600 rounded-md px-5 py-3 transition-all disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                        {status === 'paying' ? 'Opening PayHere…' : `Pay ${checkout.amount_display} with PayHere`}
                    </button>
                )}

                <p className="text-[11px] text-gray-400 mt-2 text-center">
                    Sandbox test card: 4916 2175 0100 8300 · exp 12/25 · CVV 100
                </p>
            </div>
        </motion.div>
    );
};

export default LifestoreCheckout;
