from sqlalchemy import delete, insert, select, update
from sqlalchemy_models import OrderRow


def load_order_rows():
    return select(OrderRow)


def insert_order_row():
    return insert(OrderRow)


def update_order_row():
    return update(OrderRow)


def delete_order_row():
    return delete(OrderRow)
