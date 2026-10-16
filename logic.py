from __future__ import annotations

from enum import StrEnum


class TicketStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    CLOSED = "closed"


class InvalidTransition(Exception):
    pass


_T = {("claim", TicketStatus.OPEN): TicketStatus.CLAIMED,
      ("unclaim", TicketStatus.CLAIMED): TicketStatus.OPEN,
      ("close", TicketStatus.OPEN): TicketStatus.CLOSED,
      ("close", TicketStatus.CLAIMED): TicketStatus.CLOSED,
      ("reopen", TicketStatus.CLOSED): TicketStatus.OPEN}


def next_status(status: TicketStatus, action: str) -> TicketStatus:
    try:
        return _T[(action, status)]
    except KeyError:
        raise InvalidTransition(f"cannot {action} a {status.value} ticket") from None


def is_staff(member_role_ids: set[int], support_role_ids: set[int], manage_channels: bool) -> bool:
    return manage_channels or bool(member_role_ids & support_role_ids)


def can_act(action: str, *, staff: bool, opener: bool) -> bool:
    if action == "close":
        return staff or opener
    return staff  # claim, unclaim, reopen, transcript are staff-only
