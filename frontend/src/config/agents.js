export const AGENTS = {
    // Default supervisor landing page
    workmateai: {
        id: "supervisor",
        title: "Workmate AI",
        subtitle: "Your unified SLTMobitel workplace assistant. Ask anything about HR, Finance, IT, Admin, or CIA.",
        color: "from-cyan-900 to-cyan-600",
        buttonColor: "bg-cyan-600 hover:bg-cyan-700",
        idlePrompt: "What can I help you with across the workplace?",
        disclaimer: "Workmate AI provides internal workplace information. Please verify critical details with the relevant department."
    },

    // 1. HR
    askhr: {
        id: "hr",
        title: "ASK HR",
        subtitle: "Welcome to your HR support center. Get instant assistance with leave, benefits, and policies.",
        color: "from-purple-900 to-purple-600",
        buttonColor: "bg-purple-600 hover:bg-purple-700",
        idlePrompt: "Need help with leave, benefits, or policies?",
        disclaimer: "If you need any further clarifications, please reach out to the HR department."
    },

    // 2. Finance
    askfinance: {
        id: "finance",
        title: "ASK FINANCE",
        subtitle: "Get immediate help with payroll, budgets, procurement, and financial guidelines.",
        color: "from-blue-900 to-blue-600",
        buttonColor: "bg-blue-600 hover:bg-blue-700",
        idlePrompt: "Questions on payroll, budgets, or procurement?",
        disclaimer: "If you need any further clarifications, please reach out to the Finance department."
    },

    // 3. Admin
    askadmin: {
        id: "admin",
        title: "ASK ADMIN",
        subtitle: "Support for facility management, transport, security, and general administration.",
        color: "from-gray-900 to-gray-600",
        buttonColor: "bg-gray-600 hover:bg-gray-700",
        idlePrompt: "How can I assist with facilities or transport?",
        disclaimer: "If you need any further clarifications, please reach out to the Administration department."
    },

    // 4. IT
    askit: {
        id: "it",
        title: "ASK IT",
        subtitle: "Get support for technical issues, software access, and hardware requests.",
        color: "from-sky-900 to-sky-600",
        buttonColor: "bg-sky-600 hover:bg-sky-700",
        idlePrompt: "Facing a technical, software, or hardware issue?",
        disclaimer: "If you need any further clarifications, please reach out to the IT Helpdesk."
    },

    // 5. CIA
    askcia: {
        id: "cia",
        title: "ASK CIA",
        subtitle: "Internal audit, risk management, governance, and compliance under the Internal Audit Charter.",
        color: "from-rose-900 to-rose-600",
        buttonColor: "bg-rose-600 hover:bg-rose-700",
        idlePrompt: "Ask about audit, risk, or compliance.",
        disclaimer: "If you need any further clarifications, please reach out to the Internal Audit office."
    },

    // 4. Process
    askprocess: {
        id: "process",
        title: "ASK PROCESS",
        subtitle: "Standard Operational Procedures, workflow guidance, and compliance checks.",
        color: "from-emerald-900 to-emerald-600",
        buttonColor: "bg-emerald-600 hover:bg-emerald-700",
        idlePrompt: "Looking for an SOP or workflow guidance?",
        disclaimer: "If you need any further clarifications, please reach out to the Process & Compliance team."
    },

    // 5. Enterprise
    askenterprise: {
        id: "enterprise",
        title: "ASK ENTERPRISE",
        subtitle: "Strategic insights, enterprise solutions, and corporate business intelligence.",
        color: "from-indigo-900 to-indigo-600",
        buttonColor: "bg-indigo-600 hover:bg-indigo-700",
        idlePrompt: "Explore enterprise solutions and insights.",
        disclaimer: "If you need any further clarifications, please reach out to the Enterprise Solutions team."
    },

    // 6. Ask HR (Internal SLM demo — DeepSeek-R1 1.5B via Ollama)
    askhrslm: {
        id: "askhrslm",
        title: "ASK HR(SLM)",
        subtitle: "HR support powered by SLTMobitel's internal small language model — fully on-prem inference, zero external API calls.",
        color: "from-fuchsia-900 to-fuchsia-600",
        buttonColor: "bg-fuchsia-600 hover:bg-fuchsia-700",
        idlePrompt: "Need help with leave, benefits, or policies?",
        disclaimer: "This agent runs on an internal SLM (DeepSeek-R1). Responses may be slower or less polished than cloud LLM agents."
    },

    // 7. Lifestore
    asklifestore: {
        id: "lifestore",
        title: "ASK LIFESTORE",
        subtitle: "Product details, inventory queries, and smart lifestyle solutions for customers.",
        color: "from-orange-900 to-orange-600",
        buttonColor: "bg-orange-600 hover:bg-orange-700",
        idlePrompt: "Browse products, inventory, and lifestyle picks.",
        disclaimer: "If you need any further clarifications, please reach out to the Lifestore team."
    }
};
