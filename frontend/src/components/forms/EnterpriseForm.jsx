import React, { useState } from 'react';
import { motion } from 'framer-motion';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const SERVICE_OPTIONS = [
    'Networking',
    'Akaza Multi Cloud',
    'Digital Services',
    'Data Center',
    'Internet',
    'Cyber Security',
    'Voice & Collaboration',
    'Akaza Arcadia',
];

const EnterpriseForm = () => {
    const [isSubmitted, setIsSubmitted] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState('');
    const [formData, setFormData] = useState({
        company_name: '',
        business_registration_number: '',
        contact_person: '',
        contact_number: '',
        select_service: '',
        remarks: '',
    });

    const handleChange = (e) => {
        setFormData(prev => ({ ...prev, [e.target.name]: e.target.value }));
    };

    const handleCancel = () => {
        setFormData({
            company_name: '',
            business_registration_number: '',
            contact_person: '',
            contact_number: '',
            select_service: '',
            remarks: '',
        });
        setError('');
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');

        // Client-side required-field validation
        if (
            !formData.company_name.trim() ||
            !formData.contact_person.trim() ||
            !formData.contact_number.trim() ||
            !formData.select_service
        ) {
            setError('Please fill in all required fields.');
            return;
        }

        setIsSubmitting(true);
        try {
            const response = await fetch(`${API_URL}/api/v1/enterprise/lead`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(formData),
            });

            if (!response.ok) {
                throw new Error(`Server error: ${response.status}`);
            }

            // Success
            setIsSubmitted(true);
        } catch (err) {
            console.error('Lead submission failed:', err);
            setError('Failed to submit request. Please try again.');
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
                <div className="bg-gradient-to-br from-blue-50 to-indigo-50 border border-blue-200 rounded-2xl p-6 flex flex-col items-center text-center">
                    <svg className="w-12 h-12 text-blue-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                    </svg>
                    <h3 className="text-lg font-bold text-blue-800">Request Submitted!</h3>
                    <p className="text-sm text-blue-600 mt-1">
                        Thank you. The Enterprise team will contact you shortly.
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
                <h3 className="text-lg font-bold text-gray-800 mb-4">Submit Your Service Request</h3>

                {/* Error banner */}
                {error && (
                    <div className="mb-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                        {error}
                    </div>
                )}

                <form onSubmit={handleSubmit} className="space-y-4">
                    {/* Company Name (required) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Company Name <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="text"
                            name="company_name"
                            placeholder="e.g. ABC Holdings (Pvt) Ltd"
                            value={formData.company_name}
                            onChange={handleChange}
                            required
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                    </div>

                    {/* Business Registration Number (optional) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Business Registration Number
                        </label>
                        <input
                            type="text"
                            name="business_registration_number"
                            placeholder="e.g. PV 12345"
                            value={formData.business_registration_number}
                            onChange={handleChange}
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                    </div>

                    {/* Contact Person (required) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Contact Person <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="text"
                            name="contact_person"
                            placeholder="e.g. Saman De Silva"
                            value={formData.contact_person}
                            onChange={handleChange}
                            required
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                    </div>

                    {/* Contact Number (required) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Contact Number <span className="text-red-500">*</span>
                        </label>
                        <input
                            type="text"
                            name="contact_number"
                            placeholder="e.g. 0771234567"
                            value={formData.contact_number}
                            onChange={handleChange}
                            required
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all"
                        />
                    </div>

                    {/* Select Service (radio group – required) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-2">
                            Select Service <span className="text-red-500">*</span>
                        </label>
                        <div className="space-y-2">
                            {SERVICE_OPTIONS.map((service) => (
                                <label
                                    key={service}
                                    className="flex items-center gap-2 cursor-pointer text-sm text-gray-700"
                                >
                                    <input
                                        type="radio"
                                        name="select_service"
                                        value={service}
                                        checked={formData.select_service === service}
                                        onChange={handleChange}
                                        className="accent-green-500"
                                    />
                                    {service}
                                </label>
                            ))}
                        </div>
                    </div>

                    {/* Remarks (optional) */}
                    <div>
                        <label className="block text-sm text-gray-600 mb-1">
                            Remarks / Explain Your Request
                        </label>
                        <textarea
                            name="remarks"
                            placeholder="e.g. Need 100Mbps fiber for new branch office"
                            rows={3}
                            value={formData.remarks}
                            onChange={handleChange}
                            className="w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-green-300 focus:border-green-300 transition-all resize-none"
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
                            {isSubmitting ? 'Submitting...' : 'Continue'}
                        </button>
                    </div>
                </form>
            </div>
        </motion.div>
    );
};

export default EnterpriseForm;
