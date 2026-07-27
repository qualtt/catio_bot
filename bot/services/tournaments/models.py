from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from db.models.photo_tournament import (
    PhotoTournamentEntry,
    PhotoTournamentMatch,
)


@dataclass(frozen=True)
class TournamentSourcePhoto:
    photo_id: int
    published_at: datetime
    source_post_id: int | None = None
    source_channel_history_id: int | None = None

@dataclass(frozen=True)
class TournamentVoteSubmission:
    accepted: bool
    created: bool
    tournament_id: int | None = None

@dataclass(frozen=True)
class TournamentMatchView:
    match: PhotoTournamentMatch
    left_entry: PhotoTournamentEntry
    right_entry: PhotoTournamentEntry

