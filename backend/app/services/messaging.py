"""The message thread between a student and their advisor (one thread per student). Flushes, never commits."""
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Advisor, AuditLog, Message, Schedule, Student, User
from app.services.access import Actor, assert_access

MAX_BODY = 2000


class MessageView(BaseModel):
    message_id: int
    sender_role: str  # student | advisor | admin
    sender_name: str
    body: str
    sent_at: datetime
    read_at: datetime | None
    schedule_id: uuid.UUID | None
    mine: bool


class Thread(BaseModel):
    student_id: str
    messages: list[MessageView]
    unread: int  # messages from the other side that were unread before this call


class MessageInvalid(ValueError):
    pass


def thread(db: Session, user_id: uuid.UUID, actor: Actor, student_id: str, mark_read: bool = True) -> Thread:
    """The thread, oldest first. Messages sent by the other side are marked read now."""
    assert_access(db, actor, student_id)
    rows = db.execute(select(Message, User).join(User, User.user_id == Message.sender_user_id)
                      .where(Message.student_id == student_id).order_by(Message.sent_at, Message.message_id)).all()
    unread = [m for m, sender in rows if m.read_at is None and m.sender_user_id != user_id]
    views = [_view(db, m, sender, user_id) for m, sender in rows]
    if mark_read and unread:
        now = datetime.now(timezone.utc)
        for m in unread:
            m.read_at = now
        db.flush()
    return Thread(student_id=student_id, messages=views, unread=len(unread))


def post_message(db: Session, user_id: uuid.UUID, actor: Actor, student_id: str, body: str,
                 schedule_id: uuid.UUID | None = None) -> MessageView:
    assert_access(db, actor, student_id)
    body = body.strip()
    if not body:
        raise MessageInvalid("The message cannot be empty")
    if len(body) > MAX_BODY:
        raise MessageInvalid(f"The message is {len(body)} characters; at most {MAX_BODY}")
    if schedule_id is not None:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None or schedule.student_id != student_id:
            raise MessageInvalid("That schedule does not belong to this student")
    message = Message(student_id=student_id, schedule_id=schedule_id, sender_user_id=user_id, body=body)
    db.add(message)
    db.flush()
    db.add(AuditLog(actor_type="user", actor_id=str(user_id), action="message_sent", entity_type="message",
                    entity_id=str(message.message_id),
                    details={"student_id": student_id, "schedule_id": str(schedule_id) if schedule_id else None}))
    db.flush()
    return _view(db, message, db.get(User, user_id), user_id)


def _view(db: Session, m: Message, sender: User, viewer_id: uuid.UUID) -> MessageView:
    if sender.role == "student" and sender.student_id and (s := db.get(Student, sender.student_id)):
        name = f"{s.first_name} {s.last_name}"
    elif sender.role == "advisor" and sender.advisor_id and (a := db.get(Advisor, sender.advisor_id)):
        name = f"{a.first_name} {a.last_name}"
    else:
        name = "Administrator"
    return MessageView(message_id=m.message_id, sender_role=sender.role, sender_name=name, body=m.body,
                       sent_at=m.sent_at, read_at=m.read_at, schedule_id=m.schedule_id,
                       mine=m.sender_user_id == viewer_id)
