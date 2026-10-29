# Owner-side BaaS hardening guide

A checklist and review procedures for the **authenticated, server-side** security of a
Backend-as-a-Service project — the half of an audit that the toolkit's scanning stages
**cannot and must not** do for you.

`run-audit.sh` audits a site from the outside, logged out, and only observes. That is the
right posture for a tool pointed at third-party sites, but it means several real risks are
invisible to it: what an ordinary **signed-in** user can write, whether row-level policies
restrict *which columns* a user may change, whether a CAPTCHA is actually enforced by the
server, and what your edge functions expose. Those checks need accounts, write access and
your project's own admin credentials. You run them yourself, on your own project.

This guide is written around **Supabase / PostgREST / Postgres**, because that is where the
patterns are concrete, but the principles (column-level authorization, server-side
enforcement, least-privilege grants, regression baselines) apply to any BaaS.

---

## 0. Before you start — scope and safety

These are preconditions, not suggestions. Every procedure below assumes all of them.

- **Only your own project, or one you have explicit written authorization to test.**
  Attempting to write columns you shouldn't, or probing signup on a project you don't own,
  is unauthorized modification under laws such as the US CFAA and the UK Computer Misuse
  Act — the fact that you used a "test account" is no defence.
- **Static analysis first.** Most of the important findings (which policies exist, which
  columns are writable, which grants are too broad) come from *reading* your schema and
  policies, with no probing at all. Do that before any active test.
- **Test data and test accounts only.** Never probe against real user rows. Create
  throwaway accounts and rows, and keep a note of every id you create.
- **Back up first; change in reversible steps.** Snapshot the policies/grants you are about
  to touch (see §7). Make each change a single, reversible migration with a rollback file,
  and get a human go before applying anything in production.
- **Report, never auto-apply.** This guide and the toolkit *find and explain*; a human
  decides and applies. Nothing here should run unattended against production.
- **Clean up.** Any test that creates a row or a user must delete it afterwards and verify
  the count is back to zero (§9).

---

## 1. Row-level security: column-level authorization (the "write gap")

**The risk.** Row-Level Security (RLS) decides *which rows* a user may touch. It does **not**
by itself restrict *which columns* they may set. A policy like "a participant may update
their own row" with no `WITH CHECK` and a table-wide `UPDATE` grant lets an ordinary user
set **any** column on that row — including ones the UI never exposes: a `status` /
`approved` / `verified` flag, a `role`, a `points` or `paid` field, an admin-only column on
their own registration, or a consent record's own `verified` flag. The app looks fine
because the UI never offers those fields; the API accepts them anyway.

**Find it (static — read-only).** Run these in the SQL editor against your own database.

List every policy and whether it has a `WITH CHECK` clause, flagging permissive `UPDATE` /
`ALL` / `INSERT` policies that have none:

```sql
select schemaname, tablename, policyname, cmd, permissive, roles,
       qual        as using_expr,
       with_check  as check_expr
from pg_policies
where schemaname not in ('pg_catalog','information_schema')
order by (with_check is null and cmd in ('UPDATE','ALL','INSERT')) desc,
         schemaname, tablename, policyname;
```

A row with `cmd` in `UPDATE`/`ALL`/`INSERT` and a null `check_expr` means the policy
constrains *which* rows but not *what is written to them* — the write gap.

List the column-level grants held by the anon and authenticated roles, so you can see where
`UPDATE` is table-wide rather than scoped to specific columns:

```sql
select table_schema, table_name, grantee, privilege_type, string_agg(column_name, ', ') as columns
from information_schema.role_column_grants
where grantee in ('anon','authenticated')
group by table_schema, table_name, grantee, privilege_type
order by table_schema, table_name, grantee, privilege_type;
```

Pay attention to any table where `authenticated` (or worse, `anon`) holds `UPDATE`/`INSERT`
across sensitive columns rather than a named subset.

**Confirm it (active — your own test data only).** With two throwaway accounts, have account
A `PATCH` a privileged column on a row it legitimately participates in, then read that row
back **with account B** (a different, independent account — not A's own response, which can
echo the request). If the value changed, the column is client-settable. Record only
"client-settable: yes/no", never the value. If you have a direct database connection, do the
write inside a transaction you roll back instead of using a second account.

**Fix.**
- Scope the grant: `revoke update on <table> from authenticated;` then
  `grant update (<only the columns a user may set>) on <table> to authenticated;`.
- Or constrain the policy with a `WITH CHECK` that pins the row's ownership/identity and
  forbids changing privileged columns.
- Or, where a column must never be user-set, enforce it with a `BEFORE UPDATE` trigger that
  restores the old value unless the caller is an admin — useful when the grant can't be
  narrowed cleanly (see §8 for when a trigger is the plan-independent option).
- Re-run the static queries and the two-account read-back to confirm the gap is closed.

**Standards.** OWASP API Security Top 10 — API1:2023 Broken Object Level Authorization and
API3:2023 Broken Object **Property** Level Authorization; CWE-639 (authorization bypass
through user-controlled key) and CWE-285 (improper authorization).

---

## 2. Chain analysis: over-fetch + write gap

Two findings that each look minor can combine into a serious one, so review them together.

- The toolkit's **stage 4b** (`4b-overfetch.json`, `fields_not_rendered`) tells you which
  fields a logged-out visitor's browser receives but the page never displays — often an
  internal row **id** or foreign key the UI doesn't show.
- §1 tells you which columns an ordinary signed-in user can **write** on a row, given its id.

Put them together: if a public response leaks the id of a record, and a write policy accepts
any caller who knows that id, an attacker who never had access can modify — or take over — a
record. Neither finding reads as critical alone; the chain is.

**Rank by chain.** For each write finding, note the identifier or role an attacker needs, and
whether any public (logged-out) response leaks it. A write gap whose precondition is a leaked
public id is far more serious than one that needs information only an admin has. Fix both
ends: stop returning internal identifiers the UI doesn't use (a database view or an explicit
`select` list), and enforce ownership in the policy's `WITH CHECK` so knowing the id is not
enough.

---

## 3. Auth configuration

**Find it.** The toolkit's opt-in read (`BAAS_AUTH_SETTINGS=1`, stage 4b) reports your
project's public `/auth/v1/settings`: whether public signup is open (`disable_signup`),
whether email/phone sign-ups are auto-confirmed (`mailer_autoconfirm` / `phone_autoconfirm`),
and which external providers are enabled. You can also read it yourself — it is public and
unauthenticated.

**The risk to verify.** Open signup **plus** email auto-confirm means an account is usable
without the owner ever proving they control the email address. On its own that may be
acceptable. It becomes a takeover path when a trigger (e.g. an `on_auth_user_created` hook)
links a brand-new account to an **existing** profile or record **by email address** — the
new, unverified account inherits the existing one. That trigger is invisible from outside, so
the settings endpoint can only flag the *condition*; you confirm the trigger exists by
reading your own schema.

**Fix.** Require email confirmation (turn off auto-confirm) wherever accounts gain access to
existing data; or make any email-based linking depend on a verified email; or close public
signup if the app doesn't need it. Decide per project — this is a risk condition to review,
not an automatic defect.

**Standards.** OWASP API2:2023 Broken Authentication; OWASP ASVS V2 (Authentication).

---

## 4. Server-side CAPTCHA enforcement

**This section creates and consumes auth state. Run it only against your own project, with a
reserved test email domain, and clean up afterwards (§9).**

**The lesson.** A visible CAPTCHA widget that shows "Success" does **not** mean the server
enforces it. The widget is client-side; enforcement is a server setting. It is common to see
a working Turnstile/hCaptcha widget on a form while the API still accepts requests that carry
**no** CAPTCHA token — so the form looks protected and isn't.

**Verify (own project).** Against your own auth endpoint, send a sign-in/sign-up request with
**no** CAPTCHA token, using a non-existent address at a reserved test domain
(`@example.com` or an `.invalid` address) so the attempt cannot reach a real inbox. Interpret
the result carefully:

- A `400` with a `captcha` error (e.g. `captcha_failed`) means enforcement is **on** — good.
- A `200`/normal response means enforcement is **off** — the widget is decorative.
- A `400` that is an *email/validation* error, not a captcha error, is **inconclusive**:
  some projects reject the address format *before* the captcha check. Use an address the
  validator accepts (at a domain you control or a reserved test domain) and read the error
  code, not just the status.

A second, bogus-token request separates failure modes: the provider's verify step
distinguishes "wrong secret configured" (`invalid-input-secret`) from "fake token rejected"
(`invalid-input-response`). **Caveat:** if signup is open and CAPTCHA is *not* enforced, a
sign-up request can **create a user** — confirmed immediately if auto-confirm is on. That is
why this is owner-only and why §9 cleanup is mandatory.

**Turnstile / hCaptcha gotchas worth documenting for whoever fixes it:**
- Tokens are **single-use** — the form must reset the widget after every submit, or the
  second send fails with a reused-token error.
- The widget's **allowed hostnames** must include every subdomain **and** `localhost`, or
  local/dev logins fail.
- A **test / always-pass** site key is rejected by the real secret — don't ship it.
- A **wrong secret** fails verification while the widget still shows "Success", which is
  confusing; surface the real provider error code in the UI so this is diagnosable.

**Fix.** Turn on server-side CAPTCHA enforcement for the auth endpoints, configure the real
secret, and re-run the tokenless probe before and after the change to confirm the `400`
`captcha` response appears.

---

## 5. Edge function inventory

**Not visible from outside.** Whether a function requires a JWT (`verify_jwt`) can only be
read with your project's Management API / CLI and an admin token — an external scan can see
only the response to an unauthenticated call, and only for function names it already knows.
So this is an owner-side inventory.

**Do.**
- List every deployed function and its `verify_jwt` setting (Management API / `supabase
  functions list`). For each, decide whether unauthenticated invocation is intended, and
  confirm what an unauthenticated call actually returns.
- For admin-only functions, also make an **authenticated-but-not-admin** call and confirm it
  is refused — `verify_jwt` only checks that *a* token is present, not that it is privileged.
- Flag any function invoked with a **service key and an anon key header together**: that
  combination produces a "conflicting API keys" `401` and can silently break the function
  (e.g. stop transactional emails sending). This is a deployment bug to fix, not an audit
  finding about an attacker.

---

## 6. Baselines and diffing (audit as regression)

Hardening sticks only if a later change can't quietly undo it. Keep a committed snapshot of
the security-relevant surface and fail your pipeline on any **unexplained** diff.

Snapshot (owner-side, from your own database):
- anon and authenticated **grants** (the §1 `role_column_grants` query),
- **RLS state** and policy definitions (`pg_policies`),
- **view definitions** for any view that exists to narrow what a role can read.

Store these as files in a private repo, and add a verifier that re-runs the queries and
fails CI if the output differs from the committed snapshot without an accompanying,
reviewed migration. Every intended change then shows up as a small, expected diff; an
accidental one shows up as a failure.

For the **external** metrics (security headers, TLS grade, accessibility counts, perf
scores, the over-fetch field set), the toolkit's **stage 11** (`diff-runs.py`,
`11-diff.json`) already does run-to-run diffing against `previous-run/`. This section is the
database-side equivalent, which the toolkit can't reach.

---

## 7. Snapshot queries (for §6)

Keep the output of these alongside your migrations. They are all read-only.

```sql
-- RLS enabled per table
select n.nspname as schema, c.relname as table, c.relrowsecurity as rls_enabled
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relkind = 'r' and n.nspname not in ('pg_catalog','information_schema')
order by 1, 2;

-- policies (full definitions)
select schemaname, tablename, policyname, cmd, roles, qual, with_check
from pg_policies
where schemaname not in ('pg_catalog','information_schema')
order by 1, 2, 3;

-- column grants to the client-facing roles
select table_schema, table_name, grantee, privilege_type, column_name
from information_schema.role_column_grants
where grantee in ('anon','authenticated')
order by 1, 2, 3, 4, 5;

-- view definitions
select schemaname, viewname, definition
from pg_views
where schemaname not in ('pg_catalog','information_schema')
order by 1, 2;
```

---

## 8. Recommendations should name plan/tier limits

Several fixes depend on the hosting or service **plan**, and the obvious fix may not be
available on a free tier. Say so, and give a plan-independent alternative:

- A **password-verification auth hook** or **leaked-password protection** may be a paid-tier
  feature — substitute a database trigger / function that enforces the same rule.
- **Branch protection** / required reviews may need a paid repository tier — document the
  manual review step as the interim control.
- A managed WAF or rate-limiter may be plan-gated — enforce the limit in the function or at
  the edge config you do have.

When you write up a finding, state the plan dependency explicitly and offer the alternative,
so the recommendation is actionable regardless of tier.

---

## 9. Probing hygiene and logs

- **Tag your probes.** Use a known, identifying test user-agent and keep a tight time window,
  so the operator can tell your probe traffic apart from real traffic. (The external toolkit
  does this with `AUDIT_UA` and the `run-identity` row in `0-run-status.json`.) Owner-side
  probes against your own backend show up as permission-denied bursts in the database logs —
  expect them and be ready to explain them.
- **Triage the same day.** Dashboard/log retention on a free tier can be ~24 hours, so review
  what your probes produced before it rolls off.
- **Delete what you create.** Every probe that creates a row or a user must delete it, then
  verify the count is zero — leave the project exactly as you found it.

---

## 10. How this maps to the toolkit

| Concern | External, automated (`run-audit.sh`) | Owner-side (this guide) |
|---|---|---|
| Data a logged-out visitor can **read** | Stage 4b over-fetch (shape only, no values) | — |
| Public auth settings | Stage 4b opt-in read (`BAAS_AUTH_SETTINGS=1`) | §3, plus the invisible linking-trigger check |
| Data a signed-in user can **write** | *not done — needs accounts* | §1, §2 |
| CAPTCHA actually enforced | *not done — would create accounts* | §4 |
| Edge-function JWT requirements | *not visible externally* | §5 |
| Regression over time | Stage 11 diff (external metrics) | §6 (grants / RLS / views) |

The split is deliberate: the toolkit stays external and observe-only so it is safe to point
at any authorized target, and the authenticated, state-changing checks live here, to be run
by the project owner on their own project.
