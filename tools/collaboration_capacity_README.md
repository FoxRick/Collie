# Collaboration capacity calibration

Run `collaboration_capacity.sql` only in an isolated Supabase project after applying the collaboration migration. It creates 40-, 200-, and 1,000-message fixtures with retained edits, deletes, long text, and multilingual UTF-8, reports table/index/total growth, and rolls the transaction back.

Record the measured database, collaboration, and Storage byte totals through a service-only maintenance operation before enabling admissions. The migration requires a measurement newer than 36 hours, keeps a 50 MiB admission reserve, stops before 400 MiB total database use, and caps measured collaboration relations at 250 MiB. These are conservative pilot defaults until hosted measurements are captured. Do not run this against production or infer provider Storage/Reatime usage from Postgres relation sizes.
