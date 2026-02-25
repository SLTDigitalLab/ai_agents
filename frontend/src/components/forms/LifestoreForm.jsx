import React, { useState } from 'react';
import { motion } from 'framer-motion';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const LifestoreForm = () => {
    const [isSubmitted, setIsSubmitted] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState('');
    const [formData, setFormData] = useState({
        product: '',
        fullName: '',
        deliveryAddress: '',
        phone: '',
    });

    const handleChange = (e) => {
        setFormData(prev => ({ ...prev, [e.target.name]: e.target.value }));
    };

    const handleCancel = () => {
        setFormData({ product: '', fullName: '', deliveryAddress: '', phone: '' });
        setError('');
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');

        // Client-side required-field validation
        if (!formData.fullName.trim() || !formData.deliveryAddress.trim() || !formData.phone.trim()) {
            setError('Please fill in all required fields.');
            return;
        }

        setIsSubmitting(true);
        try {
            const response = await fetch(`${API_URL}/api/v1/orders/submit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(formData),
            });

            if (!response.ok) {
                throw new Error(`Server error: ${response.status}`);
            }

            // Success
            setIsSubmitted(true);
            setFormData({ product: '', fullName: '', deliveryAddress: '', phone: '' });
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
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.4, ease: 'easeOut' }}
                className="w-full my-3"
            >
                <div className="bg-gradient-to-br from-green-50 to-emerald-50 border border-green-200 rounded-2xl p-6 flex flex-col items-center text-center">
                    <svg className="w-12 h-12 text-green-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                    </svg>
                    <h3 className="text-lg font-bold text-green-800">Order Submitted!</h3>
                    <p className="text-sm text-green-600 mt-1">
                        Thank you. The Lifestore team will contact you shortly.
                    </p>
                </div>
            </motion.div>
        );
    }

    // ── Form state ──────────────────────────────────────────────────────
    return (
        <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, ease: 'easeOut' }}
            className="w-full my-3"
        >
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
                    {/* Product (optional) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">Product</label>
                        <input
                            type="text"
                            name="product"
                            placeholder="e.g., router / Archer AX20"
                            value={formData.product}
                            onChange={handleChange}
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
        </motion.div>
    );
};

export default LifestoreForm;
