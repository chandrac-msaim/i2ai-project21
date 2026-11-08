"""SQLAlchemy models mapped exactly to db/schema.sql (schema 'advisor'). Never create or alter tables here."""
from app.models.agents import (
    AgentRun,
    AuditLog,
    ComplianceCheck,
    DegreeAudit,
    ElectiveSuggestion,
    Message,
    Notification,
    RiskFlag,
)
from app.models.base import Base
from app.models.catalog import (
    Course,
    CourseCorequisite,
    CourseOffering,
    CoursePrerequisite,
    CourseTermStat,
    CourseType,
    Instructor,
    Section,
    SectionInstructor,
    SectionMeeting,
    Subject,
    Term,
    TermSession,
)
from app.models.people import Advisor, Student, User
from app.models.program import (
    CoreRequirement,
    ElectiveRequirement,
    Program,
    University,
    RuleSetting,
    Track,
    TrackTermPlan,
)
from app.models.record import CourseWaiver, Enrollment, GradeScale, StudentDocument, StudentInterest
from app.models.schedules import Schedule, ScheduleChange, ScheduleItem
from app.models.views import CoursePopularity, StudentProgress, ViewBase

# The 39 table models (universities added in M8b), and the 2 read-only view models
TABLE_MODELS: list[type[Base]] = [mapper.class_ for mapper in Base.registry.mappers]
VIEW_MODELS: list[type[ViewBase]] = [StudentProgress, CoursePopularity]
