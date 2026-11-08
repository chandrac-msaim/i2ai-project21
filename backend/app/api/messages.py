"""Message thread between a student and their advisor. Roles: the student, their advisor; an admin may read."""
import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.api.deps import DB, AnyUser, StudentOrAdvisorUser
from app.services import messaging

router = APIRouter(prefix="/messages", tags=["messages"])


class MessageIn(BaseModel):
    body: str
    schedule_id: uuid.UUID | None = None  # optional: the schedule the message is about


@router.get("/{student_id}", response_model=messaging.Thread)
def get_thread(student_id: str, user: AnyUser, db: DB) -> messaging.Thread:
    """The thread, oldest first. Messages from the other side are marked read by this call."""
    result = messaging.thread(db, user.user_id, user, student_id)
    db.commit()
    return result


@router.post("/{student_id}", response_model=messaging.MessageView, status_code=status.HTTP_201_CREATED)
def post_message(student_id: str, body: MessageIn, user: StudentOrAdvisorUser, db: DB) -> messaging.MessageView:
    """A student writes in their own thread, an advisor in the thread of a student they advise."""
    created = messaging.post_message(db, user.user_id, user, student_id, body.body, body.schedule_id)
    db.commit()
    return created
