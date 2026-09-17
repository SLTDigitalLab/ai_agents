# Airflow — LifeStore Neo4j Graph Refresh

This folder contains an **Apache Airflow** setup that runs the LifeStore Neo4j
product-graph scraper (`backend/scripts/build_lifestore_graph.py`) on a monthly
schedule, replacing the Neo4j step of the old cron job.

**Qdrant ingestion** stays on the existing cron (`monthly_kb_refresh.py`).  
**Chat, FastAPI, and LangGraph** are unaffected.

---

## What the DAG does

DAG ID: `lifestore_neo4j_graph_refresh`


| Setting             | Value                                                        |
| ------------------- | ------------------------------------------------------------ |
| Schedule            | `0 2 1 * *` — 02:00 on the 1st of every month (Asia/Colombo) |
| Catchup             | Disabled                                                     |
| Max concurrent runs | 1                                                            |
| Timeout             | 6 hours                                                      |
| Retries             | 1, after 5 minutes                                           |


On each run, the single task `refresh_lifestore_graph` runs a bash command that
calls `build_lifestore_graph.py`. The exact command is controlled by the
`LIFESTORE_GRAPH_REFRESH_CMD` environment variable (see below).

---



## Files

```
airflow/
  dags/
    lifestore_neo4j_graph.py   # The DAG
  .env.example                 # Copy to .env and fill in
  docker-compose.yaml          # Apache official Compose (local use)
  README.md                    # This file
```

---



## Local Development Setup

> Uses the mounted `backend/` folder inside the Airflow worker.
> Writes the JSON snapshot to `/tmp` to avoid permission issues.



### 1. Prerequisites

- Docker Desktop running
- The project `.env` (repo root) has your **dev** Neo4j credentials
(`AURA_INSTANCENAME=ask-lifestore-dev` or a local Neo4j)



### 2. Create the Airflow env file

```bash
cd airflow
cp .env.example .env
```

Edit `.env`:

- Set a real `FERNET_KEY` (see generation command in the file)
- Change `_AIRFLOW_WWW_USER_PASSWORD`
- Leave `LIFESTORE_GRAPH_REFRESH_CMD` **unset** (commented out)



### 3. Start Airflow

```bash
docker compose up airflow-init   # Run once to create the DB and admin user
docker compose up -d             # Start all services
```

UI: [http://localhost:8080](http://localhost:8080) (user/password from your `.env`)

### 4. Trigger a run

1. Open the UI → **DAGs** → search `lifestore_neo4j_graph_refresh`
2. **Pause** the schedule toggle (leave it off on your laptop)
3. Click **Trigger** to run once manually
4. Open the task → **Log** to watch scrape output



### 5. Verify

In Neo4j Browser or Aura console:

```cypher
MATCH (p:Product {source: "lifestore"})
RETURN count(p) AS products
```

Expected: up to `LIFESTORE_GRAPH_PRODUCT_URL_LIMIT` products (default 5 if unset; set to 0 in project `.env` for the full catalog).

### 6. Stop

```bash
docker compose down        # Keep data
docker compose down -v     # Wipe Airflow Postgres + Redis
```

---



## Production Setup

> The bundled `docker-compose.yaml` is Apache's **local** file.  
> Do not use it directly in production without hardening (see notes at the end).



### Step 1 — Harden the Airflow env file

On the server, inside the Airflow folder (e.g. `/opt/Ask_SLT/airflow/`):

```bash
cp .env.example .env
```

Set **all** of the following in `airflow/.env`:


| Variable                      | What to set                                                                                                                                             |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRFLOW_UID`                 | Output of `id -u` on the server user that owns the files                                                                                                |
| `FERNET_KEY`                  | Generate once: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — back it up securely, never rotate casually |
| `_AIRFLOW_WWW_USER_PASSWORD`  | Strong password, not `change-me`                                                                                                                        |
| `LIFESTORE_GRAPH_REFRESH_CMD` | See Step 2 below                                                                                                                                        |


Remove or comment out `_PIP_ADDITIONAL_REQUIREMENTS` in prod; use a custom image instead (see note).

### Step 2 — Set the production bash command

The DAG reads `LIFESTORE_GRAPH_REFRESH_CMD` to decide how to run the scraper.  
In `airflow/.env` on the server, uncomment and set:

```env
LIFESTORE_GRAPH_REFRESH_CMD=cd /opt/Ask_SLT && docker compose -f docker-compose.prod.yml exec -T backend python scripts/build_lifestore_graph.py
```

**Why exec into** `backend`**?**  
The backend container already has the project `.env` (prod Neo4j credentials, scrape settings).
Running the script there avoids duplicating secrets.

**Requirement:** the Airflow worker must be able to call `docker compose exec`.  
Mount the Docker socket in `docker-compose.yaml`:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

Add this under each service that needs it (scheduler, worker). Verify Docker is accessible from the worker before the first Trigger.

### Step 3 — Wire the app env

In the **project** `.env` on the server (`/opt/Ask_SLT/.env`), set:

```env
# Stop the monthly cron from also rebuilding Neo4j
RUN_NEO4J_GRAPH_REFRESH=false
```

After editing, restart the backend container so it reloads the env:

```bash
cd /opt/Ask_SLT
docker compose up -d backend
```

Verify:

```bash
docker compose exec backend printenv RUN_NEO4J_GRAPH_REFRESH
# expected: false

docker compose exec backend printenv LIFESTORE_GRAPH_PRODUCT_URL_LIMIT
# expected: 0
```



### Step 4 — Disable Neo4j in the monthly cron

The existing cron entry is:

```cron
0 2 1 * * /opt/Ask_SLT/scripts/run_monthly_kb_refresh_prod.sh
```

That script calls `monthly_kb_refresh.py`. Because `RUN_NEO4J_GRAPH_REFRESH=false`
is now set in the app `.env`, the Neo4j step is already skipped.  
Qdrant ingestion (`RUN_QDRANT_INGESTIONS=true`) continues to run on cron.

Do not remove the cron line ,only confirm `RUN_NEO4J_GRAPH_REFRESH=false` is active.

Verify after the next cron run that the log does **not** contain:

```
LifeStore Neo4j graph rebuilt successfully.
```



### Step 5 — Start Airflow on the server

```bash
cd /opt/Ask_SLT/airflow   # or wherever the Airflow folder lives

docker compose up airflow-init
docker compose up -d
```

Wait until all services are healthy:

```bash
docker compose ps
```

UI: `http://<server-ip>:8080`

### Step 6 — First manual run (before enabling the schedule)

1. Open the UI → **DAGs** → `lifestore_neo4j_graph_refresh`
2. Keep the schedule **paused**
3. Click **Trigger**
4. Open the task log — look for:
  - `Connected to Neo4j.`
  - `LifeStore products: <N>` (should be the full catalog size, not 5 or 10)
  - No traceback at the end
5. Check prod Neo4j: `MATCH (p:Product) RETURN count(p)`



### Step 7 — Enable the schedule

Only after Step 6 succeeds:

1. **Unpause** the DAG schedule in the UI
2. The DAG now runs automatically at 02:00 on the 1st of each month (Asia/Colombo)

---



## Important: Do not run both Airflow and cron on Neo4j


| System                                        | Responsibility                                       |
| --------------------------------------------- | ---------------------------------------------------- |
| **Airflow** (`lifestore_neo4j_graph_refresh`) | Neo4j graph update                                   |
| **Cron** (`monthly_kb_refresh.py`)            | Qdrant ingest only (`RUN_NEO4J_GRAPH_REFRESH=false`) |


Running both against Neo4j at the same time can cause overlapping writes.

---



## Production Hardening Notes

The bundled `docker-compose.yaml` is Apache's official **local** development file.
Before using it in production, address the following:

- **Custom image:** replace `_PIP_ADDITIONAL_REQUIREMENTS` with a `Dockerfile`  
that installs `neo4j requests beautifulsoup4 python-dotenv` at build time.  
This avoids pip running on every container start (which can cause the API  
server health check to timeout).
- **Database passwords:** the Postgres and Redis credentials in `docker-compose.yaml`
are `airflow`/`airflow`. Change them for production.
- **JWT secret:** `AIRFLOW__API_AUTH__JWT_SECRET` defaults to `airflow_jwt_secret`.
Set a strong random value in `airflow/.env`.
- **Docker socket:** the worker needs `/var/run/docker.sock` mounted to run
`docker compose exec`. Ensure the Airflow user has permission.
- **Firewall:** the Airflow UI (port 8080) should not be publicly exposed.

---



## Environment Variable Reference



### `airflow/.env` (Airflow only)


| Variable                       | Required   | Notes                                                 |
| ------------------------------ | ---------- | ----------------------------------------------------- |
| `AIRFLOW_UID`                  | Yes        | `id -u` on Linux; `50000` on local Windows            |
| `FERNET_KEY`                   | Yes        | Generate once, back up, never change after first init |
| `_AIRFLOW_WWW_USER_USERNAME`   | Yes        | Airflow UI login                                      |
| `_AIRFLOW_WWW_USER_PASSWORD`   | Yes        | Change from `airflow` in prod                         |
| `_PIP_ADDITIONAL_REQUIREMENTS` | Local only | Remove in prod; use custom image                      |
| `LIFESTORE_GRAPH_REFRESH_CMD`  | Prod only  | `docker compose exec` command on the server           |




### Project `.env` (app, affects the graph script)


| Variable                  | Prod value | Notes                                 |
| ------------------------- | ---------- | ------------------------------------- |
| `RUN_NEO4J_GRAPH_REFRESH` | `false`    | Stops cron from also rebuilding Neo4j |
| `CLEAR_LIFESTORE_GRAPH`   | `false`    | Keep upsert behavior; do not wipe     |


