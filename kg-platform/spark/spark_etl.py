"""Step 3a - Spark ETL: validation, deduplication, aggregation, transformation.

Input : resolved documents (JSON lines)   e.g. hdfs://namenode:9000/data/resolved
Output: curated nodes / relations / documents / rejected (JSON lines) e.g. hdfs://namenode:9000/data/curated
Run   : spark-submit spark_etl.py --input <uri> --output <uri> [--min-confidence 0.5]
"""
import argparse
import sys

sys.path.insert(0, "/opt/kgp")  # kgp.schema is dependency-free
from pyspark.sql import SparkSession, functions as F, types as T

from kgp.schema import ENTITY_TYPES, PREDICATES

ap = argparse.ArgumentParser()
ap.add_argument("--input", required=True)
ap.add_argument("--output", required=True)
ap.add_argument("--min-confidence", type=float, default=0.5)
args = ap.parse_args()

spark = SparkSession.builder.appName("kgp-etl").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

entity = T.StructType([T.StructField(n, T.StringType()) for n in ("id", "name", "type")] +
                      [T.StructField("aliases", T.ArrayType(T.StringType()))])
relation = T.StructType([T.StructField(n, T.StringType()) for n in (
    "subject_id", "subject_name", "subject_type", "predicate", "object_id", "object_name", "object_type",
    "evidence")] + [T.StructField("confidence", T.DoubleType())])
schema = T.StructType([T.StructField(n, T.StringType()) for n in ("doc_id", "title", "source", "origin")] + [
    T.StructField("entities", T.ArrayType(entity)), T.StructField("relations", T.ArrayType(relation))])

df = spark.read.schema(schema).option("pathGlobFilter", "*.jsonl").json(args.input).cache()

# ---- documents ----
docs = df.select("doc_id", "title", "source", "origin").dropDuplicates(["doc_id"])

# ---- relations: validate -> deduplicate/aggregate ----
rel = df.select("doc_id", F.explode_outer("relations").alias("r")).where(F.col("r").isNotNull()).select("doc_id", "r.*")
is_valid = F.coalesce(
    F.col("subject_id").isNotNull() & F.col("object_id").isNotNull()
    & (F.col("subject_id") != F.col("object_id"))
    & F.col("predicate").isin(list(PREDICATES))
    & F.col("subject_type").isin(ENTITY_TYPES) & F.col("object_type").isin(ENTITY_TYPES)
    & (F.coalesce(F.col("confidence"), F.lit(0.0)) >= args.min_confidence), F.lit(False))
good, rejected = rel.where(is_valid), rel.where(~is_valid)

relations = (good.groupBy("subject_id", "predicate", "object_id")
             .agg(F.first("subject_name").alias("subject_name"), F.first("object_name").alias("object_name"),
                  F.max("confidence").alias("max_conf"), F.countDistinct("doc_id").alias("support"),
                  F.collect_set("doc_id").alias("sources"),
                  F.slice(F.collect_set("evidence"), 1, 3).alias("evidence"))
             .withColumn("confidence", F.least(F.lit(1.0), F.round(F.col("max_conf") + 0.05 * (F.col("support") - 1), 3)))
             .drop("max_conf"))

# ---- nodes ----
ents = df.select("doc_id", F.explode_outer("entities").alias("e")).where(F.col("e").isNotNull()).select("doc_id", "e.*")
nodes = (ents.where(F.col("id").isNotNull() & F.col("type").isin(ENTITY_TYPES))
         .groupBy("id").agg(F.first("name").alias("name"), F.first("type").alias("type"),
                            F.array_distinct(F.flatten(F.collect_list("aliases"))).alias("aliases"),
                            F.collect_set("doc_id").alias("docs"), F.count("*").alias("mentions")))
# referential integrity: keep only relations whose endpoints exist
ids = nodes.select("id")
relations = (relations.join(ids.withColumnRenamed("id", "subject_id"), "subject_id", "left_semi")
             .join(ids.withColumnRenamed("id", "object_id"), "object_id", "left_semi"))

out = args.output.rstrip("/")
for name, d in (("nodes", nodes), ("relations", relations), ("documents", docs), ("rejected", rejected)):
    d.coalesce(1).write.mode("overwrite").json(f"{out}/{name}")
print(f"[kgp-etl] documents={docs.count()} nodes={nodes.count()} relations={relations.count()} "
      f"rejected={rejected.count()}")
spark.stop()
