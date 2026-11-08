-- Sanity checks after loading:  psql -U graydi -d graydi -f db/checks.sql
SET search_path TO advisor, public;

\echo '--- rows per table'
SELECT relname AS table_name, n_live_tup AS approx_rows FROM pg_stat_user_tables WHERE schemaname='advisor' ORDER BY relname;

\echo '--- core units per track (expect 126)'
SELECT cr.track_code, count(*) AS courses, sum(c.units_default) AS core_units
FROM core_requirements cr JOIN courses c USING (course_code) GROUP BY 1;

\echo '--- units and QPA by cohort (graduated AIM12 = 144 units)'
SELECT s.entry_term, s.track_code, s.status, count(*) AS students,
       min(p.units_completed) AS min_done, max(p.units_completed) AS max_done,
       round(avg(p.units_in_progress)) AS avg_in_progress, round(avg(p.qpa), 2) AS avg_qpa
FROM students s JOIN v_student_progress p USING (student_id) GROUP BY 1,2,3 ORDER BY 1,2;

\echo '--- demo students'
SELECT s.student_id, s.first_name, s.last_name, p.units_completed, p.units_in_progress, p.qpa
FROM students s JOIN v_student_progress p USING (student_id) WHERE s.is_demo;

\echo '--- time clashes inside any student term (expect 0)'
SELECT count(*) AS clashes
FROM enrollments a JOIN enrollments b ON a.student_id = b.student_id AND a.term_code = b.term_code AND a.course_code < b.course_code
JOIN sections sa ON sa.section_id = a.section_id JOIN sections sb ON sb.section_id = b.section_id
JOIN term_sessions ta ON (ta.term_code, ta.session_code) = (sa.term_code, sa.session_code)
JOIN term_sessions tb ON (tb.term_code, tb.session_code) = (sb.term_code, sb.session_code)
JOIN section_meetings ma ON ma.section_id = sa.section_id JOIN section_meetings mb ON mb.section_id = sb.section_id
WHERE ta.start_date <= tb.end_date AND tb.start_date <= ta.end_date
  AND ma.day_of_week = mb.day_of_week AND ma.start_time < mb.end_time AND mb.start_time < ma.end_time;

\echo '--- registration status on the demo date 2026-11-07 (expect F26 closed, S27 open, M27 not_open_yet)'
SELECT term_code, registration_opens, registration_closes, status, has_offerings
FROM term_registration(date '2026-11-07') WHERE year >= 2026;

\echo '--- new student (no history, interests waiting for confirmation)'
SELECT s.student_id, s.first_name, s.last_name, s.entry_term, p.units_completed, p.units_in_progress,
       (SELECT string_agg(label || ' (' || status || ')', ', ') FROM student_interests i WHERE i.student_id = s.student_id) AS interests
FROM students s JOIN v_student_progress p USING (student_id) WHERE s.entry_term = 'S27';

\echo '--- Spring 2027 core courses (AIM12 term 2): sections and seats'
SELECT cr.course_code, c.title, count(sec.section_id) AS sections, sum(sec.seats_left) AS seats_left
FROM core_requirements cr JOIN courses c USING (course_code)
LEFT JOIN sections sec ON sec.course_code = cr.course_code AND sec.term_code = 'S27' AND sec.status = 'offered'
WHERE cr.track_code = 'AIM12' AND cr.recommended_term_seq = 2 GROUP BY 1,2 ORDER BY 1;

\echo '--- most popular electives for AIM12 (graduate level)'
SELECT p.course_code, c.title, p.track_share, p.demand, p.popularity
FROM course_popularity p JOIN courses c USING (course_code)
WHERE p.track_code = 'AIM12' AND c.level = 'graduate'
  AND p.course_code NOT IN (SELECT course_code FROM core_requirements)
ORDER BY p.popularity DESC LIMIT 10;
