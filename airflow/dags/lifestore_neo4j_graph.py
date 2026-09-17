import os
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

# Local default: script mounted into the Airflow worker.
# Production: set LIFESTORE_GRAPH_REFRESH_CMD in airflow/.env, e.g.
#   cd /opt/Ask_SLT && docker compose exec -T backend python scripts/build_lifestore_graph.py
_LOCAL_REFRESH_CMD = (
    "cd /opt/airflow/project/backend && "
    "export LIFESTORE_GRAPH_OUTPUT_FILE=/tmp/lifestore_all.json && "
    "python scripts/build_lifestore_graph.py"
)
REFRESH_CMD = os.environ.get("LIFESTORE_GRAPH_REFRESH_CMD", _LOCAL_REFRESH_CMD)

with DAG(
    dag_id="lifestore_neo4j_graph_refresh",
    start_date=pendulum.datetime(2026, 9, 1, tz="Asia/Colombo"),
    schedule="0 2 1 * *",
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(hours=6),
    },
    tags=["lifestore", "neo4j"],
) as dag:
    BashOperator(
        task_id="refresh_lifestore_graph",
        bash_command=REFRESH_CMD,
    )
