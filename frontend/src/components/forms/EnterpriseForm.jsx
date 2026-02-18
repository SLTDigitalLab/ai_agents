import React, { useState } from 'react';
import { motion } from 'framer-motion';

const EnterpriseForm = () => {
    const [isSubmitted, setIsSubmitted] = useState(false);
    const [formData, setFormData] = useState({
        serviceType: '',
        companyName: '',
        brNumber: '',
        contactPerson: '',
        email: '',
    });

    const handleChange = (e) => {
        setFormData(prev => ({ ...prev, [e.target.name]: e.target.value }));
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        setIsSubmitted(true);
    };

    if (isSubmitted) {
        return (
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.4, ease: 'easeOut' }}
                className="w-full my-3"
            >
                <div className="bg-gradient-to-br from-blue-50 to-indigo-50 border border-blue-200 rounded-2xl p-6 flex flex-col items-center text-center">
                    <svg className="w-12 h-12 text-blue-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"></path></svg>
                    <h3 className="text-lg font-bold text-blue-800">Request Submitted!</h3>
                    <p className="text-sm text-blue-600 mt-1">Thank you. The Enterprise team will contact you shortly.</p>
                </div>
            </motion.div>
        );
    }

    return (
        <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, ease: 'easeOut' }}
            className="w-full my-3"
        >
            <div className="bg-gradient-to-br from-blue-50 to-indigo-50 border border-blue-200/60 rounded-2xl p-5 shadow-sm">
                <div className="flex items-center gap-2 mb-4">
                    <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center">
                        <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-white" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M4 4a2 2 0 012-2h8a2 2 0 012 2v12a1 1 0 110 2h-3a1 1 0 01-1-1v-2a1 1 0 00-1-1H9a1 1 0 00-1 1v2a1 1 0 01-1 1H4a1 1 0 110-2V4zm3 1h2v2H7V5zm2 4H7v2h2V9zm2-4h2v2h-2V5zm2 4h-2v2h2V9z" clipRule="evenodd" />
                        </svg>
                    </div>
                    <h3 className="text-sm font-semibold text-blue-800">Enterprise Service Request</h3>
                </div>

                <form onSubmit={handleSubmit} className="space-y-3">
                    <select
                        name="serviceType" value={formData.serviceType} onChange={handleChange} required
                        className="w-full text-sm bg-white border border-blue-200 rounded-xl px-4 py-2.5 text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-300/50 focus:border-blue-300 transition-all appearance-none"
                    >
                        <option value="" disabled>Select Service Type</option>
                        <option value="dedicated_internet">Dedicated Internet</option>
                        <option value="mpls_vpn">MPLS VPN</option>
                        <option value="cloud_hosting">Cloud Hosting</option>
                        <option value="managed_security">Managed Security</option>
                        <option value="iot_solutions">IoT Solutions</option>
                        <option value="other">Other</option>
                    </select>
                    <input
                        type="text" name="companyName" placeholder="Company Name"
                        value={formData.companyName} onChange={handleChange} required
                        className="w-full text-sm bg-white border border-blue-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-300/50 focus:border-blue-300 transition-all"
                    />
                    <div className="grid grid-cols-2 gap-3">
                        <input
                            type="text" name="brNumber" placeholder="BR Number"
                            value={formData.brNumber} onChange={handleChange} required
                            className="w-full text-sm bg-white border border-blue-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-300/50 focus:border-blue-300 transition-all"
                        />
                        <input
                            type="text" name="contactPerson" placeholder="Contact Person"
                            value={formData.contactPerson} onChange={handleChange} required
                            className="w-full text-sm bg-white border border-blue-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-300/50 focus:border-blue-300 transition-all"
                        />
                    </div>
                    <input
                        type="email" name="email" placeholder="Email Address"
                        value={formData.email} onChange={handleChange} required
                        className="w-full text-sm bg-white border border-blue-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-300/50 focus:border-blue-300 transition-all"
                    />
                    <button
                        type="submit"
                        className="w-full text-sm font-medium text-white bg-gradient-to-r from-blue-500 to-indigo-600 rounded-xl py-2.5 hover:from-blue-600 hover:to-indigo-700 active:scale-[0.98] transition-all shadow-sm"
                    >
                        Submit Request
                    </button>
                </form>
            </div>
        </motion.div>
    );
};

export default EnterpriseForm;
