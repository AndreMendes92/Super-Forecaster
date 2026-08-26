"""
db.py — database setup and models
----------------------------------
Stores "watches": a saved alert like "email me when the average price
for Condo Apt in Toronto drops below $650,000".

Uses whatever DATABASE_URL points to (a free Supabase/Postgres
database in production — see README for setup) and falls back to a
local SQLite file (watches.db) when DATABASE_URL isn't set, so the
app still runs with zero setup for local testing.

IMPORTANT: on Render's free tier the filesystem is ephemeral, so the
SQLite fallback will lose data on every restart/redeploy. For real
persistent alerts, set DATABASE_URL to a real Postgres database
(Supabase's free tier works well) — see README.md.
"""

import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./watches.db")

# Some providers (Supabase, Heroku-style) hand out "postgres://" URLs;
# SQLAlchemy's psycopg driver wants "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Watch(Base):
    """One saved price alert."""

    __tablename__ = "watches"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)

    # What to track
    data_source = Column(String, nullable=False)      # "statcan" or "repliers"
    geography = Column(String, nullable=False)          # e.g. "Toronto CMA" or city name
    property_type = Column(String, nullable=True)        # e.g. "Condo Apt", or a StatCan index type
    label = Column(String, nullable=True)                 # human-readable summary, for display/emails

    # The condition
    target_price = Column(Float, nullable=False)
    direction = Column(String, nullable=False)  # "below" or "above"
    value_unit = Column(String, nullable=False, default="cad")  # "cad" (dollar price) or "index" (StatCan NHPI points)

    # State
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_notified_at = Column(DateTime, nullable=True)
    last_checked_value = Column(Float, nullable=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
