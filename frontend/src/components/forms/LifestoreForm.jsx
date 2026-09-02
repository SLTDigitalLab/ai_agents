import React, { useRef, useState } from 'react';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const PRODUCT_SEARCH_DEBOUNCE_MS = 300;

const LifestoreForm = ({ onSuccess } = {}) => {
    const [isSubmitted, setIsSubmitted] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState('');
    const [submitNotice, setSubmitNotice] = useState('');
    const [submittedData, setSubmittedData] = useState(null);
    const [formData, setFormData] = useState({
        fullName: '',
        deliveryAddress: '',
        phone: '',
        email: '',
        city: '',
        note: '',
    });

    // Product is search-and-pick, not free text: productQuery drives the input
    // display, productId is the only thing actually submitted.
    const [productQuery, setProductQuery] = useState('');
    const [productId, setProductId] = useState('');
    const [productSuggestions, setProductSuggestions] = useState([]);
    const [showProductSuggestions, setShowProductSuggestions] = useState(false);
    const productSearchTimeout = useRef(null);

    const handleChange = (e) => {
        setFormData(prev => ({ ...prev, [e.target.name]: e.target.value }));
    };

    const handleProductInputChange = (e) => {
        const value = e.target.value;
        setProductQuery(value);
        setProductId(''); // any manual edit invalidates the previous selection

        if (productSearchTimeout.current) {
            clearTimeout(productSearchTimeout.current);
        }

        if (!value.trim()) {
            setProductSuggestions([]);
            setShowProductSuggestions(false);
            return;
        }

        productSearchTimeout.current = setTimeout(async () => {
            try {
                const response = await fetch(
                    `${API_URL}/api/v1/orders/products/search?q=${encodeURIComponent(value)}`
                );
                const body = await response.json();
                setProductSuggestions(body?.results || []);
                setShowProductSuggestions(true);
            } catch (err) {
                console.error('Product search failed:', err);
            }
        }, PRODUCT_SEARCH_DEBOUNCE_MS);
    };

    const handleProductSelect = (product) => {
        setProductQuery(product.name);
        setProductId(product.product_id);
        setShowProductSuggestions(false);
        setProductSuggestions([]);
    };

    const handleCancel = () => {
        setFormData({ fullName: '', deliveryAddress: '', phone: '', email: '', city: '', note: '' });
        setProductQuery('');
        setProductId('');
        setProductSuggestions([]);
        setShowProductSuggestions(false);
        setError('');
        setSubmitNotice('');
        setSubmittedData(null);
    };

    const buildApiErrorMessage = (responseStatus, responseBody) => {
        const detail = responseBody?.detail;
        if (typeof detail === 'string' && detail.trim()) {
            return detail;
        }

        if (Array.isArray(detail) && detail.length > 0) {
            return detail
                .map((item) => item?.msg)
                .filter(Boolean)
                .join(', ');
        }

        if (typeof responseBody?.message === 'string' && responseBody.message.trim()) {
            return responseBody.message;
        }

        return `Order submission failed (HTTP ${responseStatus}). Please try again.`;
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setSubmitNotice('');

        // Client-side required-field validation
        if (
            !formData.fullName.trim() ||
            !formData.deliveryAddress.trim() ||
            !formData.phone.trim() ||
            !formData.email.trim() ||
            !formData.city.trim()
        ) {
            setError('Please fill in all required fields.');
            return;
        }

        setIsSubmitting(true);
        try {
            const response = await fetch(`${API_URL}/api/v1/orders/submit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ...formData,
                    product_id: productId || undefined,
                }),
            });

            let responseBody = null;
            try {
                responseBody = await response.json();
            } catch {
                responseBody = null;
            }

            if (!response.ok) {
                setError(buildApiErrorMessage(response.status, responseBody));
                return;
            }

            const apiStatus = (responseBody?.status || '').toLowerCase();
            if (apiStatus && apiStatus !== 'success') {
                setError(responseBody?.message || 'Order could not be completed fully. Please contact support.');
                return;
            }

            setSubmitNotice(responseBody?.message || 'Order placed successfully.');

            // Success
            const payload = { ...formData, product: productQuery };
            setSubmittedData(payload);
            setIsSubmitted(true);
            setFormData({ fullName: '', deliveryAddress: '', phone: '', email: '', city: '', note: '' });
            setProductQuery('');
            setProductId('');
            onSuccess?.(payload);
        } catch (err) {
            console.error('Order submission failed:', err);
            setError('Failed to submit order. Please try again.');
        } finally {
            setIsSubmitting(false);
        }
    };

    // ── Success state ───────────────────────────────────────────────────
    if (isSubmitted) {
        return (
            <div className="w-full my-3">
                <div className="bg-gradient-to-br from-green-50 to-emerald-50 border border-green-200 rounded-2xl p-6 flex flex-col items-center text-center">
                    <svg className="w-12 h-12 text-green-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                    </svg>
                    <h3 className="text-base font-bold text-green-800">Order Submitted!</h3>
                    <p className="text-xs text-green-600 mt-1">
                        Thank you. The Lifestore team will contact you shortly.
                    </p>
                    {submitNotice && (
                        <p className="text-xs text-green-700 mt-2 font-medium">{submitNotice}</p>
                    )}

                    {submittedData && (
                        <div className="mt-4 w-full max-w-md rounded-xl border border-green-100 bg-white/80 p-4 text-left shadow-sm">
                            <p className="text-xs font-semibold uppercase tracking-wider text-green-700 mb-3">
                                Submitted details
                            </p>
                            <div className="space-y-2 text-xs text-gray-700">
                                <div className="flex justify-between gap-3">
                                    <span className="text-gray-500">Email</span>
                                    <span className="font-medium text-gray-800 text-right">{submittedData.email}</span>
                                </div>
                                <div className="flex justify-between gap-3">
                                    <span className="text-gray-500">City / Area</span>
                                    <span className="font-medium text-gray-800 text-right">{submittedData.city}</span>
                                </div>
                                <div className="flex justify-between gap-3 items-start">
                                    <span className="text-gray-500">Order note</span>
                                    <span className="font-medium text-gray-800 text-right whitespace-pre-wrap">
                                        {submittedData.note || 'N/A'}
                                    </span>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        );
    }

    // ── Form state ──────────────────────────────────────────────────────
    return (
        <div className="w-full my-3">
            <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                {/* Title */}
                <h3 className="text-lg font-bold text-gray-800 mb-4">Please fill the form</h3>

                {/* Error banner */}
                {error && (
                    <div className="mb-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                        {error}
                    </div>
                )}

                <form onSubmit={handleSubmit} className="space-y-4">
                    {/* Product (search and pick, not free text) */}
                    <div className="relative">
                        <label className="block text-sm text-gray-600 mb-1">Product</label>
                        <input
                            type="text"
                            name="productSearch"
                            placeholder="Start typing to search products..."
                            value={productQuery}
                            onChange={handleProductInputChange}
                            onFocus={() => productSuggestions.length > 0 && setShowProductSuggestions(true)}
                            onBlur={() => setTimeout(() => setShowProductSuggestions(false), 150)}
                            autoComplete="off"
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                        {productId && (
                            <p className="text-xs text-green-600 mt-1">Selected from catalog ✓</p>
                        )}
                        {showProductSuggestions && productSuggestions.length > 0 && (
                            <ul className="absolute z-10 mt-1 w-full max-h-56 overflow-y-auto bg-white border border-gray-200 rounded-md shadow-lg">
                                {productSuggestions.map((product) => (
                                    <li key={product.product_id}>
                                        <button
                                            type="button"
                                            onMouseDown={(e) => e.preventDefault()}
                                            onClick={() => handleProductSelect(product)}
                                            className="w-full text-left px-3 py-2 text-sm hover:bg-green-50 flex justify-between gap-2"
                                        >
                                            <span className="text-gray-700">{product.name}</span>
                                            {product.price && (
                                                <span className="text-gray-400 whitespace-nowrap">{product.price}</span>
                                            )}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>

                    {/* Email (required for BizLeads) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Email <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="email"
                            name="email"
                            placeholder="e.g., saman@example.com"
                            value={formData.email}
                            onChange={handleChange}
                            required
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                    </div>

                    {/* Full Name (required) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Full Name <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="text"
                            name="fullName"
                            placeholder="e.g., Saman Perera"
                            value={formData.fullName}
                            onChange={handleChange}
                            required
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                    </div>

                    {/* City / Area (required for BizLeads) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            City / Area <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="text"
                            name="city"
                            placeholder="e.g., Colombo"
                            value={formData.city}
                            onChange={handleChange}
                            required
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                    </div>

                    {/* Delivery Address (required) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Delivery Address <span className="text-red-500">*</span>
                        </label>
                        <textarea
                            name="deliveryAddress"
                            placeholder="House No, Street, City"
                            rows={3}
                            value={formData.deliveryAddress}
                            onChange={handleChange}
                            required
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all resize-none"
                        />
                    </div>

                    {/* Order Note (optional BizLeads note) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Order Note
                        </label>
                        <textarea
                            name="note"
                            placeholder="Add delivery instructions or extra notes"
                            rows={3}
                            value={formData.note}
                            onChange={handleChange}
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all resize-none"
                        />
                    </div>

                    {/* Phone (required) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Phone <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="tel"
                            name="phone"
                            placeholder="07XXXXXXXX or +94XXXXXXXXX"
                            value={formData.phone}
                            onChange={handleChange}
                            required
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                    </div>

                    {/* Action buttons */}
                    <div className="flex justify-end gap-4 mt-4">
                        <button
                            type="button"
                            onClick={handleCancel}
                            className="text-sm text-gray-500 hover:text-gray-700 transition-colors"
                        >
                            Cancel
                        </button>
                        <button
                            type="submit"
                            disabled={isSubmitting}
                            className="text-sm font-medium text-white bg-green-300 hover:bg-green-400 rounded-md px-5 py-2 transition-all disabled:opacity-60 disabled:cursor-not-allowed"
                        >
                            {isSubmitting ? 'Submitting...' : 'Place Order'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};

export default LifestoreForm;
