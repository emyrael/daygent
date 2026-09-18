"""Gold climate report. A plain PySpark job, not a DLT pipeline."""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from shared.hashing import build_row_hash

spark = SparkSession.getActiveSession()

_RANKED_SQL = """
select
    cr.source,
    cr.station_id,
    cr.metric,
    row_number() over (partition by cr.source order by cr.metric desc) as rank
from silver.silver_observation_metrics cr
"""

_LEADERBOARD_SQL = """
select source, station_id, metric, rank
from cr_metrics_ranked
where rank <= 50
"""

_ROLLUP_SQL = """
select source, count(*) as stations, avg(metric) as mean_metric
from cr_metrics_ranked
group by source
"""


def _write_leaderboard() -> None:
    """Stage the ranked metrics, then persist the gold leaderboard."""
    spark.sql(_RANKED_SQL).createOrReplaceTempView("cr_metrics_ranked")
    leaderboard = spark.sql(_LEADERBOARD_SQL)
    leaderboard = build_row_hash(leaderboard, ["source", "station_id"])
    leaderboard.write.mode("overwrite").saveAsTable("gold.gold_station_leaderboard")


def _write_rollup() -> None:
    """Persist the per-source rollup from the same staged temp view."""
    rollup = spark.sql(_ROLLUP_SQL)
    rollup = rollup.withColumn("mean_metric", F.col("mean_metric").cast("double"))
    rollup.write.mode("overwrite").saveAsTable("gold.gold_source_rollup")


def refresh_climate_report() -> None:
    """Entry point invoked by the scheduled job."""
    _write_leaderboard()
    _write_rollup()
