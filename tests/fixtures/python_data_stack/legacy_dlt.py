"""Legacy DLT pipeline declarations."""

import dlt


@dlt.table(name="silver_customers")
def customers():
    return dlt.read("bronze_customers")


@dlt.view
def clean_customers():
    return dlt.read_stream("bronze_events")


@dlt.table(name=get_name())
def skipped_dynamic_name():
    return dlt.read(get_name())
