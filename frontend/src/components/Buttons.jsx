import React from 'react';

// The exact string sent by kb_slm_agent.py
const LEAVE_CLARIFICATION_MSG = "Do you want to check your personal leave balance? (Choose Yes for Personal Balance, No for General Leave Policies)";

export default function Buttons({ message, isLast, onSend }) {
  if (!isLast || message.type !== 'bot' || !message.text?.includes(LEAVE_CLARIFICATION_MSG)) {
    return null; 
  }

  return (
    <div className="flex flex-wrap gap-2.5 mt-3.5 animate-fadeIn">
      <button 
        onClick={() => onSend("Yes")}
        className="px-4 py-2 text-sm font-semibold tracking-wide bg-purple-600 text-white rounded-xl shadow-sm hover:bg-purple-700 hover:shadow transition-all duration-200 transform active:scale-95 border border-purple-600/20 cursor-pointer"
      >
        Yes
      </button>
      
      <button 
        onClick={() => onSend("No")}
        className="px-4 py-2 text-sm font-semibold tracking-wide bg-purple-50 text-purple-700 rounded-xl hover:bg-purple-100/80 transition-all duration-200 transform active:scale-95 border border-purple-200/40 cursor-pointer"
      >
        No
      </button>
    </div>
  );
}