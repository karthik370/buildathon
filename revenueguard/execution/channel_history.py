"""
Persistent channel outcome storage — SQLite-backed.

Channel preference learning is only meaningful if outcomes persist across
pipeline runs. A customer who received a successful SMS recovery in batch N
should be preferenced for SMS in batch N+1, not reset to the whatsapp prior.

This module provides:
    load_channel_history()    — called at start of each pipeline run
    persist_channel_history() — called after each pipeline run
    seed_customer_history()   — pre-populate from prior-batch records

The in-memory dict in channel_selector.py is populated from this store at
startup and flushed back to it after execution. The SQLite table is the
source of truth; the in-memory dict is the working cache.

Schema: one row per (customer_id, channel) — cumulative wins and total count.
"""

from __future__ import annotations

from pathlib import Path
from sqlalchemy import create_engine, Column, String, Integer, Float
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "revenueguard.db"
engine  = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Base    = declarative_base()
Session = sessionmaker(bind=engine)


class ChannelOutcomeRecord(Base):
    __tablename__ = "channel_outcomes"
    customer_id = Column(String, primary_key=True)
    channel     = Column(String, primary_key=True)
    wins        = Column(Integer, default=0)
    total       = Column(Integer, default=0)


def init_channel_db():
    Base.metadata.create_all(engine)


def load_channel_history() -> dict[str, dict[str, list[bool]]]:
    """
    Load persistent channel outcomes into the in-memory format used by
    channel_selector._channel_log.

    Returns: {customer_id: {channel: [True/False, ...]}}
    Expands wins/total back into a flat bool list for the selector's counter.
    """
    init_channel_db()
    session = Session()
    try:
        records = session.query(ChannelOutcomeRecord).all()
        log: dict[str, dict[str, list[bool]]] = {}
        for r in records:
            if r.customer_id not in log:
                log[r.customer_id] = {}
            outcomes: list[bool] = (
                [True]  * r.wins +
                [False] * (r.total - r.wins)
            )
            log[r.customer_id][r.channel] = outcomes
        return log
    finally:
        session.close()


def persist_channel_history(channel_log: dict[str, dict[str, list[bool]]]) -> None:
    """
    Merge the current run's channel outcomes into the persistent store.
    Uses cumulative upsert — wins and totals accumulate across runs.
    """
    init_channel_db()
    session = Session()
    try:
        for customer_id, channels in channel_log.items():
            for channel, outcomes in channels.items():
                wins  = sum(outcomes)
                total = len(outcomes)
                record = session.get(ChannelOutcomeRecord, (customer_id, channel))
                if record is None:
                    record = ChannelOutcomeRecord(
                        customer_id=customer_id,
                        channel=channel,
                        wins=wins,
                        total=total,
                    )
                    session.add(record)
                else:
                    record.wins  += wins
                    record.total += total
        session.commit()
    finally:
        session.close()


def seed_customer_history(
    customer_id: str,
    channel: str,
    wins: int,
    total: int,
    overwrite: bool = False,
) -> None:
    """
    Seed a customer's historical channel performance — represents outcomes
    from previous pipeline runs that are not in the current batch.

    Parameters
    ----------
    customer_id : Customer to seed
    channel     : Channel to seed (e.g., 'sms')
    wins        : Number of successful recoveries on this channel historically
    total       : Total number of outreach attempts on this channel historically
    overwrite   : If True, replace existing record; if False, skip if exists
    """
    init_channel_db()
    session = Session()
    try:
        record = session.get(ChannelOutcomeRecord, (customer_id, channel))
        if record is not None and not overwrite:
            return  # already seeded, don't overwrite
        if record is None:
            record = ChannelOutcomeRecord(
                customer_id=customer_id,
                channel=channel,
                wins=wins,
                total=total,
            )
            session.add(record)
        else:
            record.wins  = wins
            record.total = total
        session.commit()
        print(
            f"[ChannelHistory] Seeded {customer_id}: "
            f"{channel} wins={wins}/{total} "
            f"(simulates prior-batch history)"
        )
    finally:
        session.close()
