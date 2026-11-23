"""跨方言列类型：MySQL 用 BIGINT，SQLite（单测）降级 INTEGER 以支持自增主键。"""
from sqlalchemy import BigInteger, Integer

Int64PK = BigInteger().with_variant(Integer, "sqlite")
