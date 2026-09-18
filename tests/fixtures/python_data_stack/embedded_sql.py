def build_report():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO analytics.daily_sales
            SELECT *
            FROM warehouse.orders
            """
        )


def get_orders():
    cursor.execute(
        """
        SELECT *
        FROM warehouse.orders
        """
    )


def skipped_dynamic(query):
    cursor.execute(query)
    cursor.execute(f"SELECT * FROM {query}")
