"""Database models and operations for YouTube DeFi Monitor."""

import json
import aiosqlite
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class ScriptStatus(str, Enum):
    """Status of a generated script."""
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    PRODUCED = "produced"


class FactStatus(str, Enum):
    """Status of a fact verification."""
    VERIFIED = "verified"
    OUTDATED = "outdated"
    FALSE = "false"
    UNVERIFIED = "unverified"


@dataclass
class Channel:
    """YouTube channel model."""
    id: str
    name: str
    subscribers: int = 0
    last_checked: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "subscribers": self.subscribers,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }


@dataclass
class Video:
    """YouTube video model."""
    id: str
    channel_id: str
    title: str
    views: int
    published_at: datetime
    virality_score: float
    transcript: Optional[str] = None
    structure: Optional[dict] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "title": self.title,
            "views": self.views,
            "published_at": self.published_at.isoformat(),
            "virality_score": self.virality_score,
            "transcript": self.transcript,
            "structure": self.structure,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class VerifiedFact:
    """A fact extracted from video and its verification status."""
    id: Optional[int] = None
    video_id: str = ""
    claim: str = ""
    status: FactStatus = FactStatus.UNVERIFIED
    source: Optional[str] = None
    verified_value: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "video_id": self.video_id,
            "claim": self.claim,
            "status": self.status.value,
            "source": self.source,
            "verified_value": self.verified_value,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Script:
    """Generated script model."""
    id: Optional[int] = None
    source_video_id: str = ""
    topic: str = ""
    script_text: str = ""
    status: ScriptStatus = ScriptStatus.DRAFT
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_video_id": self.source_video_id,
            "topic": self.topic,
            "script_text": self.script_text,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
        }


class Database:
    """Async SQLite database wrapper."""

    def __init__(self, db_path: str = "data/monitor.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Connect to the database."""
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._create_tables()

    async def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        await self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                subscribers INTEGER DEFAULT 0,
                last_checked DATETIME
            );

            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                views INTEGER NOT NULL,
                published_at DATETIME NOT NULL,
                virality_score REAL NOT NULL,
                transcript TEXT,
                structure TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (channel_id) REFERENCES channels(id)
            );

            CREATE TABLE IF NOT EXISTS verified_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                claim TEXT NOT NULL,
                status TEXT DEFAULT 'unverified',
                source TEXT,
                verified_value TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos(id)
            );

            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_video_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                script_text TEXT NOT NULL,
                status TEXT DEFAULT 'draft',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_video_id) REFERENCES videos(id)
            );

            CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
            CREATE INDEX IF NOT EXISTS idx_videos_virality ON videos(virality_score DESC);
            CREATE INDEX IF NOT EXISTS idx_scripts_status ON scripts(status);
            CREATE INDEX IF NOT EXISTS idx_facts_video ON verified_facts(video_id);
        """)
        await self._connection.commit()

    # Channel operations
    async def upsert_channel(self, channel: Channel) -> None:
        """Insert or update a channel."""
        await self._connection.execute(
            """
            INSERT INTO channels (id, name, subscribers, last_checked)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                subscribers = excluded.subscribers,
                last_checked = excluded.last_checked
            """,
            (channel.id, channel.name, channel.subscribers, channel.last_checked),
        )
        await self._connection.commit()

    async def get_channel(self, channel_id: str) -> Optional[Channel]:
        """Get a channel by ID."""
        async with self._connection.execute(
            "SELECT * FROM channels WHERE id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Channel(
                    id=row["id"],
                    name=row["name"],
                    subscribers=row["subscribers"],
                    last_checked=datetime.fromisoformat(row["last_checked"])
                    if row["last_checked"]
                    else None,
                )
        return None

    async def get_all_channels(self) -> list[Channel]:
        """Get all channels."""
        async with self._connection.execute("SELECT * FROM channels") as cursor:
            rows = await cursor.fetchall()
            return [
                Channel(
                    id=row["id"],
                    name=row["name"],
                    subscribers=row["subscribers"],
                    last_checked=datetime.fromisoformat(row["last_checked"])
                    if row["last_checked"]
                    else None,
                )
                for row in rows
            ]

    # Video operations
    async def insert_video(self, video: Video) -> None:
        """Insert a new video."""
        await self._connection.execute(
            """
            INSERT INTO videos (id, channel_id, title, views, published_at,
                               virality_score, transcript, structure, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                views = excluded.views,
                virality_score = excluded.virality_score,
                transcript = COALESCE(excluded.transcript, videos.transcript),
                structure = COALESCE(excluded.structure, videos.structure)
            """,
            (
                video.id,
                video.channel_id,
                video.title,
                video.views,
                video.published_at,
                video.virality_score,
                video.transcript,
                json.dumps(video.structure) if video.structure else None,
                video.created_at,
            ),
        )
        await self._connection.commit()

    async def get_video(self, video_id: str) -> Optional[Video]:
        """Get a video by ID."""
        async with self._connection.execute(
            "SELECT * FROM videos WHERE id = ?", (video_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Video(
                    id=row["id"],
                    channel_id=row["channel_id"],
                    title=row["title"],
                    views=row["views"],
                    published_at=datetime.fromisoformat(row["published_at"]),
                    virality_score=row["virality_score"],
                    transcript=row["transcript"],
                    structure=json.loads(row["structure"]) if row["structure"] else None,
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
        return None

    async def get_viral_videos(self, limit: int = 10) -> list[Video]:
        """Get videos sorted by virality score."""
        async with self._connection.execute(
            "SELECT * FROM videos ORDER BY virality_score DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Video(
                    id=row["id"],
                    channel_id=row["channel_id"],
                    title=row["title"],
                    views=row["views"],
                    published_at=datetime.fromisoformat(row["published_at"]),
                    virality_score=row["virality_score"],
                    transcript=row["transcript"],
                    structure=json.loads(row["structure"]) if row["structure"] else None,
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    async def video_exists(self, video_id: str) -> bool:
        """Check if a video already exists."""
        async with self._connection.execute(
            "SELECT 1 FROM videos WHERE id = ?", (video_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def update_video_transcript(
        self, video_id: str, transcript: str, structure: Optional[dict] = None
    ) -> None:
        """Update video transcript and structure."""
        await self._connection.execute(
            """
            UPDATE videos SET transcript = ?, structure = ?
            WHERE id = ?
            """,
            (transcript, json.dumps(structure) if structure else None, video_id),
        )
        await self._connection.commit()

    # Fact operations
    async def insert_fact(self, fact: VerifiedFact) -> int:
        """Insert a verified fact."""
        cursor = await self._connection.execute(
            """
            INSERT INTO verified_facts (video_id, claim, status, source, verified_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                fact.video_id,
                fact.claim,
                fact.status.value,
                fact.source,
                fact.verified_value,
                fact.created_at,
            ),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def get_facts_for_video(self, video_id: str) -> list[VerifiedFact]:
        """Get all facts for a video."""
        async with self._connection.execute(
            "SELECT * FROM verified_facts WHERE video_id = ?", (video_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                VerifiedFact(
                    id=row["id"],
                    video_id=row["video_id"],
                    claim=row["claim"],
                    status=FactStatus(row["status"]),
                    source=row["source"],
                    verified_value=row["verified_value"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    async def get_verified_facts_for_video(self, video_id: str) -> list[VerifiedFact]:
        """Get only verified facts for a video."""
        async with self._connection.execute(
            "SELECT * FROM verified_facts WHERE video_id = ? AND status = 'verified'",
            (video_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                VerifiedFact(
                    id=row["id"],
                    video_id=row["video_id"],
                    claim=row["claim"],
                    status=FactStatus(row["status"]),
                    source=row["source"],
                    verified_value=row["verified_value"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    # Script operations
    async def insert_script(self, script: Script) -> int:
        """Insert a new script."""
        cursor = await self._connection.execute(
            """
            INSERT INTO scripts (source_video_id, topic, script_text, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                script.source_video_id,
                script.topic,
                script.script_text,
                script.status.value,
                script.created_at,
            ),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def get_script(self, script_id: int) -> Optional[Script]:
        """Get a script by ID."""
        async with self._connection.execute(
            "SELECT * FROM scripts WHERE id = ?", (script_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Script(
                    id=row["id"],
                    source_video_id=row["source_video_id"],
                    topic=row["topic"],
                    script_text=row["script_text"],
                    status=ScriptStatus(row["status"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
        return None

    async def get_pending_scripts(self) -> list[Script]:
        """Get all draft scripts."""
        async with self._connection.execute(
            "SELECT * FROM scripts WHERE status = 'draft' ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Script(
                    id=row["id"],
                    source_video_id=row["source_video_id"],
                    topic=row["topic"],
                    script_text=row["script_text"],
                    status=ScriptStatus(row["status"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    async def update_script_status(self, script_id: int, status: ScriptStatus) -> None:
        """Update script status."""
        await self._connection.execute(
            "UPDATE scripts SET status = ? WHERE id = ?",
            (status.value, script_id),
        )
        await self._connection.commit()

    async def script_exists_for_video(self, video_id: str) -> bool:
        """Check if a script already exists for a video."""
        async with self._connection.execute(
            "SELECT 1 FROM scripts WHERE source_video_id = ?", (video_id,)
        ) as cursor:
            return await cursor.fetchone() is not None
