"""Student routes: dashboard, interests, document upload. Role: student."""
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile, status
from pydantic import BaseModel

from app.api.deps import DB, LLM, StudentUser, Today
from app.services import student_portal as portal

router = APIRouter(prefix="/students/me", tags=["student"])


class InterestIn(BaseModel):
    label: str


class InterestAction(BaseModel):
    action: Literal["confirm", "remove"]


@router.get("/dashboard", response_model=portal.Dashboard)
def dashboard(user: StudentUser, db: DB, today: Today) -> portal.Dashboard:
    """Audit, progress, advisor, interests, unread messages, term picker, open schedules."""
    result = portal.dashboard(db, user.student_id, today)
    db.commit()  # the first visit may have saved the degree audit
    return result


@router.get("/interests", response_model=portal.InterestLists)
def interests(user: StudentUser, db: DB) -> portal.InterestLists:
    """Suggested (from the resume / undergrad text, waiting for the student) and confirmed interests."""
    return portal.list_interests(db, user.student_id)


@router.post("/interests", response_model=portal.InterestView, status_code=status.HTTP_201_CREATED)
def add_interest(body: InterestIn, user: StudentUser, db: DB) -> portal.InterestView:
    """A typed interest is saved at once as source 'manual', status 'confirmed', with its embedding."""
    created = portal.add_manual_interest(db, user.student_id, body.label)
    db.commit()
    return created


@router.patch("/interests/{interest_id}", response_model=portal.InterestView)
def update_interest(interest_id: int, body: InterestAction, user: StudentUser, db: DB) -> portal.InterestView:
    """confirm -> status 'confirmed'; remove -> status 'removed' (the row is kept)."""
    updated = portal.set_interest_status(db, user.student_id, interest_id, body.action)
    db.commit()
    return updated


@router.post("/documents", response_model=portal.UploadResult, status_code=status.HTTP_201_CREATED)
def upload_document(user: StudentUser, db: DB, llm: LLM, today: Today,
                    file: Annotated[UploadFile, File(description="PDF or .txt")],
                    doc_type: Annotated[Literal["resume", "undergrad_transcript"], Form()] = "resume"
                    ) -> portal.UploadResult:
    """Resume or undergraduate transcript (PDF or .txt, up to 5 MB). The text is saved and the InterestExtractor
    returns new *suggested* interests; nothing is confirmed until the student says so."""
    data = file.file.read(portal.MAX_UPLOAD_BYTES + 1)
    return portal.upload_document(db, user.student_id, doc_type, file.filename, data, llm=llm, today=today)
