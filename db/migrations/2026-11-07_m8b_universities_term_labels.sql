-- M8b: universities, and readable term names in text that was stored before this change.
-- Safe to run any number of times; no reset needed:
--   docker exec -i graydi-postgres psql -U graydi -d graydi < db/migrations/2026-11-07_m8b_universities_term_labels.sql
SET search_path = advisor, public;

CREATE TABLE IF NOT EXISTS universities (
    university_code  text PRIMARY KEY,
    name             text NOT NULL,
    short_name       text NOT NULL,
    email_domain     text UNIQUE
);
-- the demo accounts all use @demo.graydi.local, so the demo university owns that domain
INSERT INTO universities (university_code, name, short_name, email_domain)
VALUES ('CMU', 'Carnegie Mellon University', 'CMU', 'demo.graydi.local')
ON CONFLICT (university_code) DO UPDATE SET name = EXCLUDED.name, short_name = EXCLUDED.short_name;

ALTER TABLE programs ADD COLUMN IF NOT EXISTS university_code text REFERENCES universities;
UPDATE programs SET university_code = 'CMU' WHERE university_code IS NULL;
ALTER TABLE programs ALTER COLUMN university_code SET NOT NULL;

ALTER TABLE advisors ADD COLUMN IF NOT EXISTS university_code text REFERENCES universities;
UPDATE advisors SET university_code = 'CMU' WHERE university_code IS NULL;
ALTER TABLE advisors ALTER COLUMN university_code SET NOT NULL;

-- Text written before M8b names terms by code ("S27"). Replace the code with "Spring 2027" where a person reads it.
-- A code inside a longer id such as the section 94-830-S27-A4 is left alone. Logs (agent_runs, audit_log) keep codes.
DO $$
DECLARE t record; pat text;
BEGIN
  FOR t IN SELECT term_code, season || ' ' || year AS label FROM terms LOOP
    pat := '(?<![\w-])' || t.term_code || '(?![\w-])';
    UPDATE schedules SET
        ai_explanation = regexp_replace(ai_explanation, pat, t.label, 'g'),
        load_rationale = regexp_replace(load_rationale, pat, t.label, 'g')
      WHERE ai_explanation ~ pat OR load_rationale ~ pat;
    UPDATE risk_flags SET
        message = regexp_replace(message, pat, t.label, 'g'),
        rule_text = regexp_replace(rule_text, pat, t.label, 'g'),
        suggested_action = regexp_replace(suggested_action, pat, t.label, 'g')
      WHERE message ~ pat OR rule_text ~ pat OR suggested_action ~ pat;
    UPDATE elective_suggestions SET reason = regexp_replace(reason, pat, t.label, 'g') WHERE reason ~ pat;
    UPDATE compliance_checks SET detail = regexp_replace(detail, pat, t.label, 'g') WHERE detail ~ pat;
  END LOOP;
END $$;
