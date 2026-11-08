-- AIM12: the Capstone Project (94-739) is taken in term 3 (Summer), not term 2 (Spring).
-- Same change as CORE_PLAN_AIM12 in scripts/reference_data.py. Safe to run any number of times (no reset needed):
--   docker exec -i graydi-postgres psql -U graydi -d graydi < db/migrations/2026-11-07_m7b_capstone_aim12_summer.sql
SET search_path = advisor, public;
UPDATE core_requirements
SET recommended_term_seq = 3
WHERE track_code = 'AIM12' AND course_code = '94-739' AND recommended_term_seq <> 3;
