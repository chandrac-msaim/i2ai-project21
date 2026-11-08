"""Student-facing read models and actions: interests, document upload, dashboard.

Interests (DESIGN D13): suggested -> confirmed -> removed; rows are never deleted. A typed interest is saved by the
backend as source 'manual', status 'confirmed' with its embedding computed here; every change writes audit_log.
Functions flush but never commit: the API route owns the transaction.
"""
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import InterestExtractor, RequirementsAgent
from app.agents.requirements_agent import DegreeAuditOut
from app.llm import LLMClient
from app.models import Advisor, AuditLog, Enrollment, Message, Student, StudentDocument, StudentInterest, Track, User
from app.services.program_info import ProgramInfo, program_of_student
from app.services.schedule_views import current_schedule
from app.services.terms import TermPicker, term_labels, term_picker
from app.tools.records import get_current_audit
from app.tools.vector_search import embed_text

MAX_LABEL = 60
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 50_000
DOC_TYPES = ("resume", "undergrad_transcript")


class InterestInvalid(ValueError):
    """Empty label, or longer than 60 characters."""


class InterestDuplicate(ValueError):
    def __init__(self, interest: StudentInterest) -> None:
        super().__init__(f"You already have the interest '{interest.label}' ({interest.status})")
        self.interest_id, self.status = interest.interest_id, interest.status


class InterestNotFound(LookupError):
    pass


class DocumentRejected(ValueError):
    """Wrong file type, empty or unreadable file. `status_code` tells the route which HTTP status to use."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------- interests ----------

class InterestView(BaseModel):
    interest_id: int
    label: str
    source: str
    status: str
    created_at: datetime
    confirmed_at: datetime | None


class InterestLists(BaseModel):
    suggested: list[InterestView]
    confirmed: list[InterestView]


def _view(i: StudentInterest) -> InterestView:
    return InterestView(interest_id=i.interest_id, label=i.label, source=i.source, status=i.status,
                        created_at=i.created_at, confirmed_at=i.confirmed_at)


def list_interests(db: Session, student_id: str) -> InterestLists:
    rows = db.scalars(select(StudentInterest).where(StudentInterest.student_id == student_id,
                                                    StudentInterest.status != "removed")
                      .order_by(StudentInterest.interest_id)).all()
    return InterestLists(suggested=[_view(i) for i in rows if i.status == "suggested"],
                         confirmed=[_view(i) for i in rows if i.status == "confirmed"])


def add_manual_interest(db: Session, student_id: str, label: str) -> InterestView:
    label = " ".join(label.split())  # trim and collapse inner whitespace
    if not label:
        raise InterestInvalid("The interest cannot be empty")
    if len(label) > MAX_LABEL:
        raise InterestInvalid(f"The interest is {len(label)} characters; at most {MAX_LABEL}")
    existing = db.scalar(select(StudentInterest).where(StudentInterest.student_id == student_id,
                                                       func.lower(StudentInterest.label) == label.lower()))
    if existing is not None and existing.status != "removed":
        raise InterestDuplicate(existing)
    now = datetime.now(timezone.utc)
    embedding = embed_text(label)
    if existing is not None:  # the student removed it earlier and now types it again: bring the row back
        existing.status, existing.source, existing.confirmed_at, existing.embedding = "confirmed", "manual", now, embedding
        interest = existing
    else:
        interest = StudentInterest(student_id=student_id, label=label, source="manual", status="confirmed",
                                   confirmed_at=now, embedding=embedding)
        db.add(interest)
    db.flush()
    _audit(db, student_id, "interest_added", interest, {"source": "manual"})
    return _view(interest)


def set_interest_status(db: Session, student_id: str, interest_id: int, action: str) -> InterestView:
    interest = db.get(StudentInterest, interest_id)
    if interest is None or interest.student_id != student_id:
        raise InterestNotFound(f"Unknown interest {interest_id}")
    if action == "confirm":
        interest.status, interest.confirmed_at = "confirmed", datetime.now(timezone.utc)
        if interest.embedding is None:
            interest.embedding = embed_text(interest.label)
        audit = "interest_confirmed"
    elif action == "remove":
        interest.status = "removed"  # the row stays: history, and the extractor will not suggest it again
        audit = "interest_removed"
    else:
        raise ValueError("action must be 'confirm' or 'remove'")
    db.flush()
    _audit(db, student_id, audit, interest, {})
    return _view(interest)


def _audit(db: Session, student_id: str, action: str, interest: StudentInterest, details: dict) -> None:
    db.add(AuditLog(actor_type="user", actor_id=student_id, action=action, entity_type="student_interest",
                    entity_id=str(interest.interest_id), details={"label": interest.label, **details}))
    db.flush()


# ---------- documents ----------

class UploadResult(BaseModel):
    document_id: uuid.UUID
    doc_type: str
    filename: str | None
    characters: int
    method: str  # llm | keywords | none: how the interests were read
    suggested: list[InterestView]  # new interests, status 'suggested' (never confirmed here)


def extract_text(filename: str, data: bytes) -> str:
    """Text of a .txt or .pdf upload (pypdf for PDFs)."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentRejected(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB", 413)
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        if data[:5] != b"%PDF-":
            raise DocumentRejected("This file is not a PDF", 415)
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise DocumentRejected("The PDF is password protected")
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except PyPdfError as exc:
            raise DocumentRejected(f"The PDF could not be read: {exc}") from exc
    elif name.endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
    else:
        raise DocumentRejected("Upload a PDF or a .txt file", 415)
    text = text.replace("\x00", "").strip()
    if not text:
        raise DocumentRejected("No text found in the file (a scanned image cannot be read)")
    return text[:MAX_TEXT_CHARS]


def save_document(db: Session, student_id: str, doc_type: str, filename: str | None, text: str) -> StudentDocument:
    if doc_type not in DOC_TYPES:
        raise DocumentRejected(f"doc_type must be one of {', '.join(DOC_TYPES)}", 422)
    doc = StudentDocument(student_id=student_id, doc_type=doc_type, filename=filename, text_content=text)
    db.add(doc)
    db.flush()
    db.add(AuditLog(actor_type="user", actor_id=student_id, action="document_uploaded", entity_type="student_document",
                    entity_id=str(doc.document_id), details={"doc_type": doc_type, "filename": filename,
                                                             "characters": len(text)}))
    db.flush()
    return doc


def upload_document(db: Session, student_id: str, doc_type: str, filename: str | None, data: bytes,
                    llm: LLMClient | None = None, today: date | None = None) -> UploadResult:
    """Save the text, run the InterestExtractor (one LLM call, keyword fallback) and return the new suggestions."""
    doc = save_document(db, student_id, doc_type, filename, extract_text(filename or "", data))
    db.commit()  # the document stays even if the extractor fails
    run = InterestExtractor(db, llm=llm, today=today).run(student_id=student_id)
    labels = {i.label.lower() for i in run.output.new}
    new = [_view(i) for i in db.scalars(select(StudentInterest).where(
        StudentInterest.student_id == student_id, StudentInterest.status == "suggested")
        .order_by(StudentInterest.interest_id)) if i.label.lower() in labels]
    return UploadResult(document_id=doc.document_id, doc_type=doc_type, filename=filename,
                        characters=len(doc.text_content), method=run.output.method, suggested=new)


# ---------- dashboard ----------

class StudentSummary(BaseModel):
    student_id: str
    first_name: str
    last_name: str
    email: str
    track_code: str
    entry_term: str
    entry_term_label: str
    university_name: str  # dashboard subtitle
    academic_terms: int  # terms in the program ("term 2 of 3"), from the track
    program: ProgramInfo  # university, college and degree of the student's program


class AdvisorSummary(BaseModel):
    advisor_id: str
    name: str
    email: str
    title: str


class Progress(BaseModel):
    units_completed: Decimal
    units_in_progress: Decimal
    units_remaining: Decimal
    total_units: int
    qpa: Decimal | None
    min_qpa: Decimal
    below_min_qpa: bool
    next_term: str | None
    next_term_label: str | None
    projected_graduation_term: str | None
    projected_graduation_label: str | None
    elective_units_left: Decimal


class OpenSchedule(BaseModel):
    term_code: str
    term_label: str
    schedule_id: uuid.UUID
    version: int
    status: str


class Dashboard(BaseModel):
    student: StudentSummary
    is_new_student: bool
    audit: DegreeAuditOut
    progress: Progress
    advisor: AdvisorSummary | None
    interests: InterestLists
    unread_messages: int
    terms: TermPicker
    schedules: list[OpenSchedule]  # per plannable term: the open (draft / submitted) schedule, else the newest decided one


def dashboard(db: Session, student_id: str, today: date) -> Dashboard:
    student = db.get(Student, student_id)
    audit_json = get_current_audit(db, student_id)
    if audit_json is None:  # first visit: run the audit once (no LLM) so the dashboard has numbers
        RequirementsAgent(db, today=today).run(student_id=student_id)
        audit_json = get_current_audit(db, student_id)
    audit = DegreeAuditOut.model_validate(audit_json)
    advisor = db.get(Advisor, student.advisor_id) if student.advisor_id else None
    picker = term_picker(db, today)
    labels = term_labels(db)
    program = program_of_student(db, student_id)
    schedules = [OpenSchedule(term_code=s.term_code, term_label=labels.get(s.term_code, s.term_code), schedule_id=s.schedule_id, version=s.version, status=s.status)
                 for t in picker.plannable if (s := current_schedule(db, student_id, t.term_code))]
    return Dashboard(
        student=StudentSummary(student_id=student.student_id, first_name=student.first_name,
                               last_name=student.last_name, email=student.email, track_code=student.track_code,
                               entry_term=student.entry_term,
                               entry_term_label=labels.get(student.entry_term, student.entry_term),
                               university_name=program.university.name,
                               academic_terms=db.get(Track, student.track_code).academic_terms,
                               program=program),
        is_new_student=not db.scalar(select(func.count()).select_from(Enrollment).where(
            Enrollment.student_id == student_id)),
        audit=audit,
        progress=Progress(units_completed=audit.units_completed, units_in_progress=audit.units_in_progress,
                          units_remaining=audit.units_remaining, total_units=audit.total_units, qpa=audit.qpa,
                          min_qpa=audit.min_qpa, below_min_qpa=audit.below_min_qpa, next_term=audit.next_term,
                          next_term_label=labels.get(audit.next_term) if audit.next_term else None,
                          projected_graduation_term=audit.projected_graduation_term,
                          projected_graduation_label=(labels.get(audit.projected_graduation_term)
                                                      if audit.projected_graduation_term else None),
                          elective_units_left=audit.elective_units_left),
        advisor=AdvisorSummary(advisor_id=advisor.advisor_id, name=f"{advisor.first_name} {advisor.last_name}",
                               email=advisor.email, title=advisor.title) if advisor else None,
        interests=list_interests(db, student_id), unread_messages=unread_for_student(db, student_id),
        terms=picker, schedules=schedules)


def unread_for_student(db: Session, student_id: str) -> int:
    """Messages from an advisor (or admin) that the student has not read yet."""
    return db.scalar(select(func.count()).select_from(Message).join(User, User.user_id == Message.sender_user_id)
                     .where(Message.student_id == student_id, Message.read_at.is_(None),
                            User.role != "student")) or 0
