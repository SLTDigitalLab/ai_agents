import React, { useState } from 'react';
import { useMsal } from '@azure/msal-react';

import { getApiIdToken } from '../../apiAuth';
import {
    HELPDESK_LIMITS,
    HELPDESK_PRIORITIES,
    helpdeskErrorMessage,
    validateHelpdeskForm,
} from '../../helpdeskTicketForm';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const inputClass =
    'w-full border border-gray-300 rounded-md p-2 text-sm text-gray-700 placeholder:text-gray-400 ' +
    'focus:outline-none focus:ring-2 focus:ring-purple-300 focus:border-purple-300 transition-all';

// Remember a submitted ticket per chat message, so reloading the page shows
// the result instead of an empty form (which could create a second incident).
const readSubmitted = (key) => {
    if (!key) return null;
    try {
        return JSON.parse(localStorage.getItem(key) || 'null');
    } catch {
        return null;
    }
};

const HelpdeskTicketForm = ({ prefillDescription = '', storageKey = '' }) => {
    const { instance, accounts } = useMsal();
    const [submitted, setSubmitted] = useState(() => readSubmitted(storageKey));
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState('');
    const [formData, setFormData] = useState({
        description: prefillDescription.slice(0, HELPDESK_LIMITS.description),
        incident_type: '',
        incident_sub_type: '',
        priority: 'Low',
    });

    const handleChange = (e) => {
        setFormData((prev) => ({ ...prev, [e.target.name]: e.target.value }));
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (isSubmitting) return;
        setError('');

        const validationError = validateHelpdeskForm(formData);
        if (validationError) {
            setError(validationError);
            return;
        }

        setIsSubmitting(true);
        try {
            const token = await getApiIdToken(instance, accounts[0]);
            if (!token) {
                setError('Please sign in to create a ticket.');
                return;
            }
            const response = await fetch(`${API_URL}/api/v1/helpdesk_dev/incidents`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({
                    description: formData.description.trim(),
                    incident_type: formData.incident_type.trim(),
                    incident_sub_type: formData.incident_sub_type.trim(),
                    priority: formData.priority,
                }),
            });

            let data = null;
            try {
                data = await response.json();
            } catch {
                data = null;
            }

            if (!response.ok || !data?.incident_id) {
                setError(helpdeskErrorMessage(response.status, data));
                return;
            }

            setSubmitted(data);
            if (storageKey) {
                try {
                    localStorage.setItem(storageKey, JSON.stringify(data));
                } catch {
                    // Storage full/blocked — the success card still shows now.
                }
            }
        } catch (err) {
            console.error('Helpdesk ticket submission failed:', err);
            setError('Could not reach the server. Please check your connection and try again.');
        } finally {
            setIsSubmitting(false);
        }
    };

    // ── Success state ───────────────────────────────────────────────────
    if (submitted) {
        return (
            <div className="w-full my-3">
                <div className="bg-gradient-to-br from-purple-50 to-indigo-50 border border-purple-200 rounded-2xl p-6 flex flex-col items-center text-center">
                    <svg className="w-12 h-12 text-purple-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                    </svg>
                    <h3 className="text-base font-bold text-purple-800">Your support ticket has been created!</h3>
                    <p className="text-xs text-purple-600 mt-1">
                        Our support team will review it shortly. Keep this ID to check your ticket's status.
                    </p>
                    <div className="mt-4 p-3 bg-white rounded-lg border border-purple-100 shadow-sm w-full max-w-xs">
                        <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Ticket ID</p>
                        <p className="text-lg font-mono font-bold text-purple-700">{submitted.incident_id}</p>
                    </div>
                    <div className="mt-4 w-full max-w-md rounded-xl border border-purple-100 bg-white/80 p-4 text-left shadow-sm space-y-2 text-xs text-gray-700">
                        {[
                            ['Description', submitted.description],
                            ['Incident type', submitted.incident_type],
                            ['Sub-type', submitted.incident_sub_type],
                            ['Priority', submitted.priority],
                        ].map(([label, value]) => (
                            <div key={label} className="flex justify-between gap-3 items-start">
                                <span className="text-gray-500 shrink-0">{label}</span>
                                <span className="font-medium text-gray-800 text-right whitespace-pre-wrap break-words">
                                    {value || 'N/A'}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        );
    }

    // ── Form state ──────────────────────────────────────────────────────
    return (
        <div className="w-full my-3">
            <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                <h3 className="text-lg font-bold text-gray-800 mb-4">Create a Support Ticket</h3>

                {error && (
                    <div role="alert" className="mb-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-2">
                        {error}
                    </div>
                )}

                <form onSubmit={handleSubmit} className="space-y-4">
                    <div>
                        <label htmlFor="hd-description" className="block text-sm text-gray-600 mb-1">
                            Description <span className="text-red-500">*</span>
                        </label>
                        <textarea
                            id="hd-description"
                            name="description"
                            rows={3}
                            placeholder="Describe the issue — what's happening and when it started"
                            value={formData.description}
                            onChange={handleChange}
                            maxLength={HELPDESK_LIMITS.description}
                            required
                            className={inputClass}
                        />
                        <p className="text-xs text-gray-400 text-right">
                            {formData.description.length}/{HELPDESK_LIMITS.description}
                        </p>
                    </div>

                    <div>
                        <label htmlFor="hd-type" className="block text-sm text-gray-600 mb-1">
                            Incident Type <span className="text-red-500">*</span>
                        </label>
                        <input
                            id="hd-type"
                            type="text"
                            name="incident_type"
                            placeholder="e.g. SOA"
                            value={formData.incident_type}
                            onChange={handleChange}
                            maxLength={HELPDESK_LIMITS.incident_type}
                            required
                            className={inputClass}
                        />
                    </div>

                    <div>
                        <label htmlFor="hd-subtype" className="block text-sm text-gray-600 mb-1">
                            Incident Sub-Type <span className="text-red-500">*</span>
                        </label>
                        <input
                            id="hd-subtype"
                            type="text"
                            name="incident_sub_type"
                            placeholder="e.g. Resubmission issue"
                            value={formData.incident_sub_type}
                            onChange={handleChange}
                            maxLength={HELPDESK_LIMITS.incident_sub_type}
                            required
                            className={inputClass}
                        />
                    </div>

                    <div>
                        <label htmlFor="hd-priority" className="block text-sm text-gray-600 mb-1">
                            Priority <span className="text-red-500">*</span>
                        </label>
                        <select
                            id="hd-priority"
                            name="priority"
                            value={formData.priority}
                            onChange={handleChange}
                            className={inputClass}
                        >
                            {HELPDESK_PRIORITIES.map((p) => (
                                <option key={p} value={p}>{p}</option>
                            ))}
                        </select>
                    </div>

                    <button
                        type="submit"
                        disabled={isSubmitting}
                        className="w-full bg-purple-600 hover:bg-purple-700 disabled:opacity-60 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-md py-2 transition-all"
                    >
                        {isSubmitting ? 'Creating ticket…' : 'Create Ticket'}
                    </button>
                    {isSubmitting && (
                        <p className="text-xs text-gray-500 text-center">
                            This can take up to a minute. Please keep this page open.
                        </p>
                    )}
                </form>
            </div>
        </div>
    );
};

export default HelpdeskTicketForm;
