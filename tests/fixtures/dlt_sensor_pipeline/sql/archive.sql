-- Plain SQL archival job joining onto the silver assets built in Python.
create table gold.gold_observation_archive as
select
    metrics.source,
    metrics.station_id,
    metrics.metric,
    hourly.mean_value
from silver.silver_observation_metrics as metrics
left join silver.silver_sensor_hourly as hourly
    on metrics.station_id = hourly.station_id;
