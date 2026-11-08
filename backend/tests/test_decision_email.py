"""The decision email HTML (docs/mockups/07-approval-email.png). Pure: no database, no LLM."""
from datetime import date
from decimal import Decimal

from app.services.decision_email import email_subject, opening_line, render_html
from app.tools.schedules import DecisionEmailFacts, EmailRow


def facts(**over) -> DecisionEmailFacts:
    base = dict(
        student_first="Daniel", student_name="Daniel Okafor", student_email="daniel.okafor@demo.graydi.local",
        advisor_name="Dr. Morgan Lee", advisor_short="Dr. Lee", advisor_email="morgan.lee@demo.graydi.local",
        term_label="Spring 2027", university_name="Carnegie Mellon University", college="Heinz College",
        program_title="Master of Science in Artificial Intelligence Systems Management",
        program_name="Artificial Intelligence Systems Management",
        registration_closes=date(2026, 12, 4), projected_graduation="Summer 2027",
        rows=[EmailRow(course_code="94-815", title="Agentic Technologies", when="Mon, Wed 9:30", units=Decimal("6.0")),
              EmailRow(course_code="95-767", title="Cybersecurity <for> AI", when="Thu 18:30", units=Decimal("6.0"))],
        total_units=Decimal("12.0"))
    base.update(over)
    return DecisionEmailFacts(**base)


def html(approved=True, comment="Approved.", **over) -> str:
    return render_html(facts(**over), approved=approved, paragraph="Your advisor has approved your schedule.",
                       comment=comment, subject="Your Spring 2027 schedule was approved", from_addr="advising@demo.graydi.local",
                       schedule_url="http://localhost:5173/schedule/S27")


def test_approved_email_follows_the_mockup_blocks():
    out = html()
    for text in ("From", "To", "Cc", "Subject", "Daniel Okafor", "Dr. Morgan Lee", "Approved",
                 "Hi Daniel, your Spring 2027 schedule is approved", "Comment from Dr. Lee", "Your courses",
                 "12 units", "94-815", "Mon, Wed 9:30", "What&#8217;s next", "Dec 4, 2026",
                 "Projected graduation: Summer 2027", "Open my schedule", "http://localhost:5173/schedule/S27"):
        assert text in out, text
    assert out.startswith("<!doctype html>")


def test_email_names_the_term_and_the_university_in_header_and_footer():
    out = html()
    assert out.count("Carnegie Mellon University · Heinz College · Master of Science in Artificial Intelligence Systems Management") == 2
    assert "Spring 2027" in out and "Summer 2027" in out  # graduation is a label too, not "M27"
    assert "M27" not in out
    body = out.split("<body", 1)[1]
    assert "S27" not in body.replace("/schedule/S27", "")  # the code appears only inside the link


def test_rejected_email_shows_the_reason_and_an_edit_link():
    out = html(approved=False, comment="Please take 95-888 first.")
    assert "Rejected" in out and "was not approved" in out and "Edit my schedule" in out
    assert "Please take 95-888 first." in out and "Send it to Dr. Lee again" in out
    assert "Open my schedule" not in out and "Projected graduation" not in out


def test_everything_from_data_is_escaped():
    out = html(comment="<script>alert(1)</script>", student_first="<b>Dan</b>")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<b>Dan</b>" not in out and "Cybersecurity &lt;for&gt; AI" in out


def test_no_comment_means_no_comment_box_and_missing_dates_do_not_break_it():
    out = html(comment="  ", registration_closes=None, projected_graduation=None, advisor_name=None,
               advisor_short=None, advisor_email=None)
    assert "Comment from" not in out and "Register for these sections." in out
    assert "Cc" not in out.split("Subject")[0].split("To<")[-1] and "your advisor" in out


# ---------- M8d: the university is in the subject and the opening line, read from the data ----------

def test_subject_names_the_university_and_the_term_label():
    assert email_subject("Carnegie Mellon University", "Spring 2027", True) == \
        "Carnegie Mellon University: Your Spring 2027 schedule was approved"
    assert email_subject("Carnegie Mellon University", "Spring 2027", False) == \
        "Carnegie Mellon University: Your Spring 2027 schedule needs changes"
    assert email_subject("Zeta University", "Fall 2027", True).startswith("Zeta University: Your Fall 2027")
    assert email_subject("", "Spring 2027", True) == "Your Spring 2027 schedule was approved"  # no university known


def test_opening_line_names_university_program_and_term():
    ok = opening_line("Carnegie Mellon University", "Artificial Intelligence Systems Management", "Spring 2027", True)
    assert ok == "Carnegie Mellon University Artificial Intelligence Systems Management advising has approved your Spring 2027 schedule."
    no = opening_line("Zeta University", "Data Science", "Fall 2027", False)
    assert no == "Zeta University Data Science advising has reviewed your Fall 2027 schedule and it needs changes."
    assert opening_line("", "", "Spring 2027", True) == "Your advisor has approved your Spring 2027 schedule."


def test_html_email_has_the_opening_line_header_and_footer_with_university_and_term():
    out = html()
    opening = "Carnegie Mellon University Artificial Intelligence Systems Management advising has approved your Spring 2027 schedule."
    assert opening in out
    assert out.index(opening) < out.index("Your courses")  # it opens the body
    assert out.count("Carnegie Mellon University · Heinz College") == 2  # header and footer
    other = render_html(facts(university_name="Zeta University", college="Faculty of Data", program_name="Data Science",
                              program_title="Master of Data Science"), approved=False, paragraph="p", comment="Fix X.",
                        subject="Zeta University: Your Spring 2027 schedule needs changes",
                        from_addr="a@b.c", schedule_url="http://x/schedule/S27")
    assert "Zeta University Data Science advising has reviewed your Spring 2027 schedule and it needs changes." in other
    assert "Carnegie Mellon" not in other and other.count("Zeta University · Faculty of Data · Master of Data Science") == 2
