import pandas as pd


def load_pandas_orders():
    return pd.read_sql("SELECT * FROM warehouse.orders", connection)


def load_pandas_query():
    return pd.read_sql_query("SELECT * FROM warehouse.orders", connection)


def load_pandas_table():
    return pd.read_sql_table("orders", connection, schema="warehouse")


def skipped_pandas(sql):
    return pd.read_sql(sql, connection)
