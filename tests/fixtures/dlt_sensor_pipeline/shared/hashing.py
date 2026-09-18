"""Row-hash helpers imported by both the pipeline and the job modules."""

from pyspark.sql import functions as F


def build_row_hash(df, columns):
    """Add a deterministic hash column built from `columns`."""
    parts = [F.coalesce(F.col(name).cast("string"), F.lit("")) for name in columns]
    return df.withColumn("row_hash", F.sha2(F.concat_ws("||", *parts), 256))


class RowHasher:
    """Class import: the call resolves to no function node and is dropped."""

    def __init__(self, columns):
        self.columns = columns

    def apply(self, df):
        """Delegate to the module-level helper."""
        return build_row_hash(df, self.columns)
