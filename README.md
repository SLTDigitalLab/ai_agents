# Ask SLT - SLTMobitel AI Assistants

**Ask SLT** is a suite of AI-powered enterprise assistants designed for SLTMobitel employees. It provides specialized AI agents for different departments (HR, IT, Finance, Enterprise, etc.) to handle both general knowledge queries and personalized API-driven actions (e.g., checking leave balances).

## Architecture

The project is divided into a **Frontend** and a **Backend**, orchestrated together using Docker Compose.

- **Frontend**: A modern React application built with Vite, TailwindCSS, and Framer Motion for smooth animations. It uses Microsoft Authentication (MSAL) for secure employee login.
- **Backend**: A Python FastAPI service powered by [LangChain](https://www.langchain.com/) and [LangGraph](https://langchain-ai.github.io/langgraph/). It orchestrates multiple AI "Archetypes" (supervisors) that can intelligently decide whether to query a vector database for documents or call external SLTMobitel ERP APIs.
- **Databases**:
  - **Qdrant**: Used as the primary Vector Database for Retrieval-Augmented Generation (RAG) over uploaded documents.
  - **PostgreSQL (with pgvector)**: Used for storing chat history, user sessions, and persistent agent state.

## Features

- **Multi-Agent Architecture**: Dedicated agents for different domains (e.g., "Ask HR" for leave policies and balances).
- **Tool Calling (RAG + APIs)**: Agents can dynamically choose between searching the document knowledge base or hitting live external APIs based on the user's question.
- **Secure Authentication**: Integrated with Azure AD / Microsoft Accounts to ensure only authorized SLTMobitel employees have access.
- **Persistent Memory**: Chat threads are saved in PostgreSQL, allowing users to resume previous conversations seamlessly.

---

## Prerequisites

To run this application, you need to have the following installed on your machine:

1. **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (Docker and Docker Compose)
2. **Git**

## How to Run Locally

The easiest way to get the entire stack (Frontend, Backend, PostgreSQL, and Qdrant) running is to use Docker Compose. 

### 1. Clone the repository

```bash
git clone https://github.com/SLTDigitalLab/ai_agents.git
cd ai_agents
```

### 2. Configure Environment Variables

Create a file named `.env` in the root directory and ensure all required API keys are populated. The Docker Compose file will automatically mount these to the specific containers.

**Example `.env`**:
```env
GOOGLE_API_KEY="your_gemini_api_key"
FRONTEND_URL="http://localhost:3000"
BACKEND_URL="http://localhost:8000"
# Add other necessary Azure MSAL, LangChain, or Database credentials here
```

### 3. Start the Application

From the root directory containing the `docker-compose.yml` file, run:

```bash
docker-compose up --build -d
```

This command will:
- Pull the necessary Database images (PostgreSQL and Qdrant).
- Build the Python Backend image and install requirements.
- Build the React Frontend image and install NPM packages.
- Start all services in the background.

### 4. Access the Application

Once the containers are successfully running, you can access the following services in your browser:

- **Frontend Application**: [http://localhost:3000](http://localhost:3000)
- **Backend API Docs (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Qdrant Dashboard**: [http://localhost:6333/dashboard](http://localhost:6333/dashboard)

### 5. Stopping the Application

To shut down all running services without losing your database data, run:

```bash
docker-compose down
```

To shut down and **completely wipe** the database volumes (start completely fresh next time), run:

```bash
docker-compose down -v
```

## Development

If you prefer to run the services separately without Docker (for faster local development):

**Backend**:
```bash
cd backend
python -m venv venv
venv\Scripts\activate  # On Windows
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
*(Note: You will still need Qdrant and Postgres running, which you can start with `docker-compose up -d db_postgres db_qdrant`)*

**Frontend**:
```bash
cd frontend
npm install
npm run dev
```

---
*Developed by SLT Digital Lab*
