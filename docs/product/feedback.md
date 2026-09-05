# In-app feedback

**Status:** implemented; backend provisioning and live inbox verification required before release.

The desktop sidebar has a **Submit Feedback** button, including when collapsed.
It opens a keyboard-accessible dialog with one message field and Send. No account
or email address is required. Only the typed text and a random submission ID leave
the app; conversations, logs, device identifiers, and account details are excluded.
The message limit is 4,000 characters. A failed send keeps the draft in the open
dialog. Cancel discards the draft; drafts are not saved to disk.

The guarded Electron bridge submits to
`https://feedback.heycollie.com/api/feedback`. The renderer cannot choose the
destination or email recipient. The Cloudflare Worker in `tools/feedback/` stores
the message in a private Supabase table, then sends a plain-text team notification
via Resend. Success means the row was saved and Resend accepted the notification;
it does not prove delivery to the mailbox. A mail failure leaves the row available
in Supabase and returns an error so the user can retry. Repeated sends of the same
unchanged draft reuse the submission ID: database writes are deduplicated, and
Resend deduplicates notifications within its 24-hour idempotency window.

## Provisioning and release gate

This is a dedicated API Worker in the existing Cloudflare account; the website
repository and website Worker do not need changes. The app and this service are
reviewed together in the main app repository, but deployed separately.

1. Review and run `tools/feedback/schema.sql` once in the existing Supabase
   project. It enables RLS and denies both anonymous and authenticated client
   access. Confirm both roles cannot select or insert; the server role can.
2. Use Wrangler from `tools/feedback/` (`npx wrangler@4.92.0`). Verify the
   configured rate-limit namespace is unused or allocate another namespace.
   The binding limits each IP to three requests per minute per Cloudflare
   location; this is approximate abuse control, not a global quota.
3. Configure Worker secrets with `wrangler secret put`: `SUPABASE_URL`,
   `SUPABASE_SECRET_KEY` (server secret key or legacy service-role key),
   `RESEND_API_KEY`, `FEEDBACK_FROM` (verified sender), and `FEEDBACK_TO`
   (team inbox). Never put these in the desktop build or committed files.
4. Deploy with `npx wrangler@4.92.0 deploy --config wrangler.jsonc`. This creates
   the `feedback.heycollie.com` custom domain on the existing Cloudflare zone.
5. From a preview/packaged app, submit a test message and confirm one database
   row and one email, then check offline failure/retry and collapsed navigation.
   Do not release the desktop feature until this live check passes.

The endpoint fails closed when secrets or rate limiting are unavailable. IPs
are used transiently by the Cloudflare limiter and are not stored in the feedback
table. Hosting providers may retain normal infrastructure request logs. The
service has no public read endpoint and no user-controlled recipient. Restrict
team access to feedback, and handle deletion/retention through Supabase and the
team mailbox under the product's privacy policy.

## Local checks

Run `node --test tools/feedback/worker.test.mjs` from the repository root. In
`collie-ui/`, run the feedback/sidebar tests, `npm run typecheck`, and
`npm run build`. Worker tests use mocked upstreams and do not send real email.

References: [Cloudflare rate limiting](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/),
[Supabase API access](https://supabase.com/docs/guides/api/securing-your-api),
[Resend send API](https://resend.com/docs/api-reference/emails/send-email).
