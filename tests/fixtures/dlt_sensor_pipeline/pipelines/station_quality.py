"""Station uptime scoring. Nothing imports this module or is imported by it."""

import dlt
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()

_UPTIME_SQL = """
select
    sq_audit.station_id,
    sq_audit.uptime_ratio,
    sq_registry.commissioned_on
from sq_station_audit sq_audit
left join sq_station_registry sq_registry
    on sq_audit.station_id = sq_registry.station_id
"""


@dlt.table(name="silver.silver_station_quality")
def silver_station_quality():
    """Persisted quality scores consumed by the observations module."""
    spark.read.table("bronze.bronze_station_audit").createOrReplaceTempView("sq_station_audit")
    spark.read.table("silver.silver_station_registry").createOrReplaceTempView(
        "sq_station_registry"
    )
    scored = spark.sql(_UPTIME_SQL)
    return scored.withColumn("uptime_ratio", F.col("uptime_ratio").cast("double"))
