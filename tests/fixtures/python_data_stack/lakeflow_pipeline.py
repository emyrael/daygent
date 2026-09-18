"""Lakeflow / Spark Declarative Pipelines aggregate."""

from pyspark import pipelines as dp


@dp.materialized_view(name="gold.order_metrics")
def order_metrics():
    return spark.read.table("silver.orders")
