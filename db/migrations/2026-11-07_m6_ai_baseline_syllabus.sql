-- M6 catch-up for databases created before these columns were added to db/schema.sql.
-- Safe to run any number of times; no data is changed. Apply without a reset:
--   docker exec -i graydi-postgres psql -U graydi -d graydi < db/migrations/2026-11-07_m6_ai_baseline_syllabus.sql
SET search_path = advisor, public;
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS ai_baseline jsonb;
ALTER TABLE courses   ADD COLUMN IF NOT EXISTS syllabus_url text;
