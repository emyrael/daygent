"""Air-quality sensor observations: normalization, hourly rollup, and metrics.

Synthetic DLT module written to exercise Daygent's static resolution. The long
SQL bodies are the point: lineage has to survive embedded Spark SQL, temp-view
aliases, literal loops, and helper functions without executing anything.
"""

import dlt
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from shared.hashing import build_row_hash

spark = SparkSession.getActiveSession()


_NORMALIZED_READINGS_SQL = """
select
    rn_raw.* except(unit),
    coalesce(rn_units.unit_canonical, rn_raw.unit) as unit
from rn_readings_raw rn_raw
left join rn_seed_unit_conversion rn_units
    on lower(trim(rn_raw.unit)) = lower(trim(rn_units.unit_reported))
"""

_READINGS_SHAPED_SQL = """
with hourly as (
    select
        reading.`fk_station_id`,
        reading.`station_id`,
        date_trunc('hour', reading.reading_ts) as reading_hour,
        reading.unit,
        avg(reading.raw_value) as mean_value
    from rn_readings_normalized reading
    where reading.`station_id` is not null
    group by 1, 2, 3, 4
),
coverage as (
    select
        hourly.`fk_station_id`,
        count(*) as sample_count
    from hourly
    group by hourly.`fk_station_id`
)
select
    hourly.*,
    coverage.sample_count
from hourly
left join coverage on hourly.`fk_station_id` = coverage.`fk_station_id`
"""

_STATION_JOIN_SQL = """
select
    sr_devices.device_id,
    sr_sites.site_id,
    sr_faults.fault_count
from sr_Devices sr_devices
left join sr_Sites sr_sites on sr_devices.site_id = sr_sites.site_id
left join sr_Faults sr_faults on sr_devices.device_id = sr_faults.device_id
"""

_STATION_READINGS_SQL = """
select
    sr_readings.`fk_station_id`,
    sr_readings.station_id,
    sr_readings.unit,
    sr_joined.site_id,
    sr_joined.fault_count
from sr_readings_shaped sr_readings
inner join sr_station_join sr_joined
    on sr_readings.`fk_station_id` = sr_joined.device_id
"""

_DAILY_ROLLUP_SQL = """
select
    sd_readings.`fk_station_id`,
    sd_readings.station_id,
    sd_readings.unit,
    sd_readings.mean_value
from sd_readings_shaped sd_readings
"""

_OBSERVATION_METRICS_SQL = """
select 'station' as source, om_station.station_id, om_station.fault_count as metric
from om_sensor_hourly om_station
union all
select 'daily' as source, om_daily.station_id, om_daily.mean_value as metric
from om_sensor_daily om_daily
union all
select 'quality' as source, om_quality.station_id, om_quality.uptime_ratio as metric
from om_station_quality om_quality
"""

# Deliberate syntax error: the CTE never closes. Daygent must warn and keep
# scanning rather than fail the file or invent a relation from the fragment.
_ARCHIVE_ROLLUP_SQL = """
with archived(select station_id, sum(sample_count) as samples
    from silver.silver_station_quality group by station_id
select * from archived
"""


def _apply_calibration(df):
    """Overlay late calibration offsets onto a normalized reading set."""
    offsets = spark.read.table("bronze.bronze_calibration_offsets")
    return df.join(offsets, on="station_id", how="left")


@dlt.view(name="v_unit_conversion")
def v_unit_conversion():
    """Seed table mapping reported measurement units onto canonical ones."""
    seed = spark.read.table("bronze.bronze_unit_conversion")
    seed = seed.withColumn("unit_canonical", F.trim(F.col("unit_canonical")))
    return seed


@dlt.view(name="v_readings_normalized")
def v_readings_normalized():
    """Normalize raw readings against the unit seed, then shape them hourly."""
    spark.read.table("bronze.bronze_sensor_readings_raw").createOrReplaceTempView(
        "rn_readings_raw"
    )
    spark.read.table("v_unit_conversion").createOrReplaceTempView("rn_seed_unit_conversion")
    spark.sql(_NORMALIZED_READINGS_SQL).createOrReplaceTempView("rn_readings_normalized")
    return spark.sql(_READINGS_SHAPED_SQL)


@dlt.view(name="v_station_readings_all")
def v_station_readings_all():
    """Attach device, site, and fault dimensions to the shaped readings."""
    spark.read.table("v_readings_normalized").createOrReplaceTempView("sr_readings_shaped")
    dimensions = {
        "sr_Devices": "silver.silver_device_registry",
        "sr_Sites": "silver.silver_site_registry",
        "sr_Faults": "silver.silver_device_faults",
    }
    for view, table in dimensions.items():
        spark.read.table(table).createOrReplaceTempView(view)
    spark.sql(_STATION_JOIN_SQL).createOrReplaceTempView("sr_station_join")
    return spark.sql(_STATION_READINGS_SQL)


@dlt.view(name="v_archive_rollup_broken")
def v_archive_rollup_broken():
    """Unparseable SQL on purpose: proves the scan degrades gracefully."""
    return spark.sql(_ARCHIVE_ROLLUP_SQL)


@dlt.table(name="silver.silver_sensor_hourly")
def silver_sensor_hourly():
    """Persisted hourly sensor output."""
    df = spark.read.table("v_station_readings_all")
    df = df.filter(F.col("fault_count") == 0)
    return _apply_calibration(df)


@dlt.table(name="silver.silver_sensor_daily")
def silver_sensor_daily():
    """Persisted daily rollup of the same normalized readings."""
    spark.read.table("v_readings_normalized").createOrReplaceTempView("sd_readings_shaped")
    return spark.sql(_DAILY_ROLLUP_SQL)


@dlt.table(name="silver.silver_observation_metrics")
def silver_observation_metrics():
    """Union the persisted silver datasets into comparable metrics.

    `silver.silver_station_quality` is declared in another module with no Python
    import between them, so this only links if dataset identity is resolved
    repository-wide.
    """
    sources = {
        "om_sensor_hourly": "silver.silver_sensor_hourly",
        "om_sensor_daily": "silver.silver_sensor_daily",
        "om_station_quality": "silver.silver_station_quality",
    }
    for view, table in sources.items():
        spark.read.table(table).createOrReplaceTempView(view)
    metrics = spark.sql(_OBSERVATION_METRICS_SQL)
    return build_row_hash(metrics, ["source", "station_id"])


def _partition_name():
    """Runtime-built dataset name. Nothing here is statically resolvable."""
    return "gold.gold_" + spark.conf.get("pipeline.region") + "_partition"


@dlt.table(name=_partition_name())
def regional_partition():
    """Dynamic table name: nothing static to record, so nothing is emitted."""
    return spark.read.table(_partition_name())
