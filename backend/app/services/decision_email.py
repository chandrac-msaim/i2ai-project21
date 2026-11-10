"""HTML for the approval / rejection email (docs/mockups/07-approval-email.png). Pure functions: no database, no LLM.

Used by Agent 5 (the real email, next to the plain-text part) and by the advisor's "Preview email" modal.
Everything that comes from data is escaped. Inline styles only, because mail clients ignore stylesheets.
Colors are the design tokens of docs/UI_SPEC.md section 1.
"""
from decimal import Decimal
from html import escape

from app.tools.schedules import DecisionEmailFacts

_FONT = "'IBM Plex Sans',-apple-system,'Segoe UI',Arial,sans-serif"
_MONO = "'IBM Plex Mono',Consolas,monospace"


def email_subject(university_name: str, term_label: str, approved: bool) -> str:
    """"Carnegie Mellon University: Your Spring 2027 schedule was approved" / "... needs changes". All from the data."""
    what = "was approved" if approved else "needs changes"
    return f"{university_name + ': ' if university_name else ''}Your {term_label} schedule {what}"


def opening_line(university_name: str, program_name: str, term_label: str, approved: bool) -> str:
    """"<university> <program> advising has approved your Spring 2027 schedule." (a rejection: "... and it needs changes")."""
    who = " ".join(x for x in (university_name, program_name) if x)
    sender = f"{who} advising" if who else "Your advisor"
    if approved:
        return f"{sender} has approved your {term_label} schedule."
    return f"{sender} has reviewed your {term_label} schedule and it needs changes."


def _n(value: Decimal) -> str:
    return f"{Decimal(value).normalize():f}"


def _row(label: str, value: str, bold: bool = False) -> str:
    weight = "600" if bold else "400"
    return (f'<tr><td style="color:#5f6670;padding:5px 0;width:80px;vertical-align:top">{label}</td>'
            f'<td style="color:#121519;padding:5px 0;font-weight:{weight}">{value}</td></tr>')


def render_html(facts: DecisionEmailFacts, *, approved: bool, paragraph: str, comment: str | None, subject: str,
                from_addr: str, schedule_url: str) -> str:
    e = escape
    advisor = e(facts.advisor_short or "your advisor")
    term = e(facts.term_label)
    pill_bg, pill_fg, pill = (("#e2f3e9", "#1f6b3a", "&#10003; Approved") if approved
                              else ("#f6dede", "#a32d2d", "&#10005; Rejected"))
    title = (f"Hi {e(facts.student_first)}, your {term} schedule is approved" if approved
             else f"Hi {e(facts.student_first)}, your {term} schedule was not approved")

    opening = e(opening_line(facts.university_name, facts.program_name, facts.term_label, approved))
    program_line = e(" · ".join(x for x in (facts.university_name, facts.college, facts.program_title) if x))
    header = "".join([
        _row("From", f"Gray DI Degree Pathway Advisor &lt;{e(from_addr)}&gt;"),
        _row("To", f"{e(facts.student_name)} &lt;{e(facts.student_email)}&gt;"),
        _row("Cc", e(facts.advisor_name or "")) if facts.advisor_name else "",
        _row("Subject", e(subject), bold=True),
    ])

    comment_box = ""
    if comment and comment.strip():
        comment_box = (
            '<div style="background:#f3f4f6;border-radius:10px;padding:16px 18px;margin:20px 0">'
            f'<div style="font-weight:600;margin-bottom:4px">Comment from {advisor}</div>'
            f'<div style="white-space:pre-line">{e(comment.strip())}</div></div>')

    course_rows = "".join(
        '<tr style="border-top:1px solid #e6e8ec">'
        f'<td style="padding:12px 0;font-family:{_MONO};font-weight:500;width:72px;vertical-align:top">{e(r.course_code)}</td>'
        f'<td style="padding:12px 8px 12px 0">{e(r.title)}</td>'
        f'<td style="padding:12px 0;color:#5f6670;text-align:right;white-space:nowrap">{e(r.when)}</td></tr>'
        for r in facts.rows)

    closes = facts.registration_closes.strftime("%b %d, %Y").replace(" 0", " ") if facts.registration_closes else None
    if approved:
        steps = [f"Register for these sections{f' by {e(closes)}' if closes else ''}."]
        if facts.projected_graduation:
            steps.append(f"Projected graduation: {e(facts.projected_graduation)}.")
        button = "Open my schedule"
    else:
        steps = ["Read the comment above and change your schedule.",
                 f"Send it to {advisor} again{f' before registration closes on {e(closes)}' if closes else ''}."]
        button = "Edit my schedule"
    steps_html = "".join(f'<li style="margin:4px 0">{s}</li>' for s in steps)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(subject)}</title></head>
<body style="margin:0;background:#e9ecef;font-family:{_FONT};color:#121519;font-size:15px;line-height:1.5">
<div style="max-width:640px;margin:24px auto;background:#ffffff;border:1px solid #e6e8ec;border-radius:14px;overflow:hidden">
  <div style="background:#f8f9fb;border-bottom:1px solid #e6e8ec;padding:20px 28px"><table style="border-collapse:collapse;font-size:15px">{header}</table></div>
  <div style="padding:28px">
    <table style="border-collapse:collapse"><tr>
      <td style="background:#0e5a5c;color:#ffffff;width:40px;height:40px;border-radius:8px;text-align:center;font-size:20px">&#8599;</td>
      <td style="padding-left:12px;font-weight:600;font-size:17px">Gray DI Degree Pathway Advisor</td></tr></table>
    <p style="margin:10px 0 0;color:#5f6670;font-size:14px">{program_line}</p>
    <div style="display:inline-block;background:{pill_bg};color:{pill_fg};border-radius:999px;padding:6px 16px;font-weight:600;margin:20px 0 8px">{pill}</div>
    <h1 style="font-size:28px;line-height:1.2;margin:8px 0 12px">{title}</h1>
    <p style="font-weight:600;margin:0 0 8px">{opening}</p>
    <p style="color:#5f6670;margin:0">{e(paragraph)}</p>
    {comment_box}
    <h2 style="font-size:16px;margin:24px 0 4px">Your courses &middot; {_n(facts.total_units)} units</h2>
    <table style="border-collapse:collapse;width:100%">{course_rows}</table>
    <h2 style="font-size:16px;margin:24px 0 4px">What&#8217;s next</h2>
    <ol style="color:#5f6670;margin:0;padding-left:22px">{steps_html}</ol>
    <p style="margin:24px 0"><a href="{e(schedule_url)}" style="display:inline-block;background:#0e5a5c;color:#ffffff;text-decoration:none;font-weight:600;border-radius:8px;padding:14px 24px">{button}</a></p>
    <p style="border-top:1px solid #e6e8ec;padding-top:16px;margin:0;color:#5f6670;font-size:13px">Questions? Contact {advisor} using your university&#8217;s usual advising channel. Students can read advisor messages in the app; replies are not supported yet. If a schedule is rejected, this same email shows the reason and a link to edit the draft.</p>
    <p style="margin:12px 0 0;color:#5f6670;font-size:13px">{program_line}</p>
  </div>
</div>
</body></html>"""
