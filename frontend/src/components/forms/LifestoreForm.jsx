import React, { useState } from 'react';
import { motion } from 'framer-motion';

const LifestoreForm = () => {
    const [isSubmitted, setIsSubmitted] = useState(false);
    const [formData, setFormData] = useState({
        productName: '',
        customerName: '',
        nicNumber: '',
        contactNumber: '',
        deliveryAddress: '',
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
                <div className="bg-gradient-to-br from-green-50 to-emerald-50 border border-green-200 rounded-2xl p-6 flex flex-col items-center text-center">
                    <svg className="w-12 h-12 text-green-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"></path></svg>
                    <h3 className="text-lg font-bold text-green-800">Order Submitted!</h3>
                    <p className="text-sm text-green-600 mt-1">Thank you. The Lifestore team will contact you shortly.</p>
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
            <div className="bg-gradient-to-br from-emerald-50 to-teal-50 border border-emerald-200/60 rounded-2xl p-5 shadow-sm">
                <div className="flex items-center gap-2 mb-4">
                    <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center">
                        <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-white" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M10 2a4 4 0 00-4 4v1H5a1 1 0 00-.994.89l-1 9A1 1 0 004 18h12a1 1 0 00.994-1.11l-1-9A1 1 0 0015 7h-1V6a4 4 0 00-4-4zm2 5V6a2 2 0 10-4 0v1h4zm-6 3a1 1 0 112 0 1 1 0 01-2 0zm7-1a1 1 0 100 2 1 1 0 000-2z" clipRule="evenodd" />
                        </svg>
                    </div>
                    <h3 className="text-sm font-semibold text-emerald-800">Lifestore Order Form</h3>
                </div>

                <form onSubmit={handleSubmit} className="space-y-3">
                    <input
                        type="text" name="productName" placeholder="Product Name"
                        value={formData.productName} onChange={handleChange} required
                        className="w-full text-sm bg-white border border-emerald-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-emerald-300/50 focus:border-emerald-300 transition-all"
                    />
                    <input
                        type="text" name="customerName" placeholder="Customer Name"
                        value={formData.customerName} onChange={handleChange} required
                        className="w-full text-sm bg-white border border-emerald-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-emerald-300/50 focus:border-emerald-300 transition-all"
                    />
                    <div className="grid grid-cols-2 gap-3">
                        <input
                            type="text" name="nicNumber" placeholder="NIC Number"
                            value={formData.nicNumber} onChange={handleChange} required
                            className="w-full text-sm bg-white border border-emerald-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-emerald-300/50 focus:border-emerald-300 transition-all"
                        />
                        <input
                            type="tel" name="contactNumber" placeholder="Contact Number"
                            value={formData.contactNumber} onChange={handleChange} required
                            className="w-full text-sm bg-white border border-emerald-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-emerald-300/50 focus:border-emerald-300 transition-all"
                        />
                    </div>
                    <textarea
                        name="deliveryAddress" placeholder="Delivery Address" rows={2}
                        value={formData.deliveryAddress} onChange={handleChange} required
                        className="w-full text-sm bg-white border border-emerald-200 rounded-xl px-4 py-2.5 text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-emerald-300/50 focus:border-emerald-300 transition-all resize-none"
                    />
                    <button
                        type="submit"
                        className="w-full text-sm font-medium text-white bg-gradient-to-r from-emerald-500 to-teal-600 rounded-xl py-2.5 hover:from-emerald-600 hover:to-teal-700 active:scale-[0.98] transition-all shadow-sm"
                    >
                        Submit Order
                    </button>
                </form>
            </div>
        </motion.div>
    );
};

export default LifestoreForm;
