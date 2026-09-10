from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

with DAG(
    dag_id="lifestore_neo4j_graph_refresh",
    start_date=datetime(2026, 9, 1),
    schedule=None,
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
    bash_command=(
        "cd /opt/airflow/project/backend && "
        "export LIFESTORE_GRAPH_OUTPUT_FILE=/tmp/lifestore_all.json && "
        "python scripts/build_lifestore_graph.py"
    ),
)