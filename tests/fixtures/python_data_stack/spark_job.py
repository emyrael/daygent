"""PySpark job: literal table read/write through a function boundary."""

TABLE = "raw.orders"


def transform_orders():
    df = spark.read.table(TABLE)
    df.write.mode("overwrite").saveAsTable("silver.orders")


def skipped_dynamic(table_name, query):
    spark.table(table_name)
    spark.sql(query)
    spark.sql(f"SELECT * FROM {table_name}")
    spark.read.table(os.getenv("TABLE"))
