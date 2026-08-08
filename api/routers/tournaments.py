from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import get_current_user
from bot.services.tournaments import (
    get_current_tournament,
    get_latest_completed_tournament,
    get_next_open_match_for_user,
    get_user_tournament_champion_entry,
    submit_tournament_vote,
    tournament_period_label,
    tournament_results_text,
)
from db.crud import get_tournament_voter_count
from db.database import async_session
from db.models.photo_tournament import TOURNAMENT_COMPLETED
from db.models.user import User

router = APIRouter(prefix="/tournaments", tags=["Tournaments"])


class MatchEntryResponse(BaseModel):
    id: int
    photo_id: int
    image_url: str


class ActiveMatchResponse(BaseModel):
    match_id: int
    round_number: int
    match_number: int
    left_entry: MatchEntryResponse
    right_entry: MatchEntryResponse


class TournamentInfoResponse(BaseModel):
    id: int
    type: str
    status: str
    period_label: str
    voter_count: int
    active_match: ActiveMatchResponse | None = None
    champion_photo_id: int | None = None
    results_summary: str | None = None


class VoteRequest(BaseModel):
    match_id: int
    chosen_entry_id: int


class VoteResponse(BaseModel):
    success: bool
    created: bool
    message: str


@router.get("/active", response_model=TournamentInfoResponse)
async def get_active_tournament(current_user: Annotated[User, Depends(get_current_user)]):
    async with async_session() as session:
        tournament = await get_current_tournament(session)
        if not tournament:
            tournament = await get_latest_completed_tournament(session)

        if not tournament:
            raise HTTPException(
                status_code=status.HTTP_444_RESPONSE_NOT_FOUND, detail="No active or completed tournament found"
            )

        voter_count = await get_tournament_voter_count(session, tournament.id)

        if tournament.status == TOURNAMENT_COMPLETED:
            results_text = await tournament_results_text(session, tournament)
            winner_photo_id = tournament.winner_photo_id
            return TournamentInfoResponse(
                id=tournament.id,
                type=tournament.type.value if hasattr(tournament.type, "value") else str(tournament.type),
                status=tournament.status,
                period_label=tournament_period_label(tournament),
                voter_count=voter_count,
                active_match=None,
                champion_photo_id=winner_photo_id,
                results_summary=results_text,
            )

        # TOURNAMENT_RUNNING
        view = await get_next_open_match_for_user(session, user_id=current_user.id, tournament_id=tournament.id)
        active_match = None
        champion_photo_id = None

        if view:
            active_match = ActiveMatchResponse(
                match_id=view.match.id,
                round_number=view.match.round.round_number,
                match_number=view.match.match_number,
                left_entry=MatchEntryResponse(
                    id=view.left_entry.id,
                    photo_id=view.left_entry.photo_id,
                    image_url=f"/api/v1/photos/{view.left_entry.photo_id}/image",
                ),
                right_entry=MatchEntryResponse(
                    id=view.right_entry.id,
                    photo_id=view.right_entry.photo_id,
                    image_url=f"/api/v1/photos/{view.right_entry.photo_id}/image",
                ),
            )
        else:
            champion_entry = await get_user_tournament_champion_entry(
                session, user_id=current_user.id, tournament_id=tournament.id
            )
            if champion_entry:
                champion_photo_id = champion_entry.photo_id

        return TournamentInfoResponse(
            id=tournament.id,
            type=tournament.type.value if hasattr(tournament.type, "value") else str(tournament.type),
            status=tournament.status,
            period_label=tournament_period_label(tournament),
            voter_count=voter_count,
            active_match=active_match,
            champion_photo_id=champion_photo_id,
        )


@router.post("/vote", response_model=VoteResponse)
async def vote_in_tournament(
    payload: VoteRequest,
    current_user: Annotated[User, Depends(get_current_user)],
):
    async with async_session() as session:
        result = await submit_tournament_vote(
            session,
            match_id=payload.match_id,
            chosen_entry_id=payload.chosen_entry_id,
            user_id=current_user.id,
        )

    if not result.accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vote not accepted or match closed")

    msg = "Vote recorded" if result.created else "Already voted"
    return VoteResponse(success=True, created=result.created, message=msg)
