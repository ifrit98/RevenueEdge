# Dashboard Specification — Revenue Edge

Cross-phase UI spec for the Next.js dashboard. Each section notes which phase
introduces it and what backend endpoints it consumes.

**Stack**: Next.js 15 + Supabase Auth + Tailwind CSS + shadcn/ui.
**Port source**: `SMB-MetaPattern/apps/dashboard/` provides the auth shell, layout, and Supabase client patterns. Strip all real-estate pages; keep the admin/settings skeleton.

---

## Authentication

| Item | Endpoint / source | Phase |
|---|---|---|
| Supabase email/password sign-up + sign-in | `@supabase/ssr` | 1 |
| Magic link (passwordless) | `@supabase/ssr` | 1 |
| OAuth (Google) | Supabase Auth provider config | 2 |
| Session refresh + redirect on 401 | Supabase client middleware | 1 |
| `x-business-id` header injection | From `business_members` lookup after login | 1 |

---

## Layout

```
┌─────────────────────────────────────────────────┐
│  Sidebar                         │  Main         │
│                                  │               │
│  Logo / business name            │               │
│  ──────────────────              │               │
│  Dashboard        (Phase 1)      │  <page>       │
│  Inbox            (Phase 1)      │               │
│  Leads            (Phase 2)      │               │
│  Quotes           (Phase 3)      │               │
│  Bookings         (Phase 4)      │               │
│  Reactivation     (Phase 5)      │               │
│  Knowledge        (Phase 2)      │               │
│  Settings         (Phase 1)      │               │
│    ├─ Business                   │               │
│    ├─ Channels                   │               │
│    ├─ Integrations (Phase 4)     │               │
│    ├─ Services     (Phase 3)     │               │
│    └─ Team                       │               │
└─────────────────────────────────────────────────┘
```

---

## Pages

### 1. Dashboard (`/`) — Phase 1

**Purpose**: At-a-glance KPIs for the business owner.

**Data source**: `GET /v1/metrics?days=30`

**Layout**:
- Top row: 4–6 metric cards (today + trend sparkline for the last 30 days)
  - Missed calls
  - Recovered leads
  - Avg first-response time (seconds → formatted as "< 30s", "2m 14s", etc.)
  - Inbound leads
  - Quotes sent
  - Bookings created
- Second row: daily bar chart overlaying missed_calls (red) vs recovered_leads (green) for the last 30 days
- Phase 5 addition: before/after comparison panel using `GET /v1/metrics/comparison`

**Interactions**:
- Click any metric card → navigates to the relevant list page (e.g., "Missed calls" → Inbox filtered by `current_intent = missed_call`)
- Date range picker (7d / 30d / 90d / custom)

---

### 2. Inbox (`/inbox`) — Phase 1

**Purpose**: Operator's primary work surface. Lists all conversations that need attention.

**Data source**: `GET /v1/conversations?status=...&limit=50`

**Layout**:
- Left panel: conversation list
  - Each row: contact name/phone, channel icon (phone/SMS), last message preview (truncated), relative timestamp, urgency badge, intent pill
  - Filter tabs: All | Open | Awaiting Customer | Awaiting Human | Escalated | Resolved
  - Sort: most-recent-message first (default) or urgency-first
  - Search: by contact name, phone, or message content (future: full-text via Supabase)
- Right panel: conversation detail (selected conversation)
  - **Header**: contact name, phone, email, tags, channel type
  - **Message thread**: chronological, alternating bubbles (inbound = left/gray, outbound = right/blue, system = center/light)
  - **Decision card** (below header or as a sidebar tab):
    - Intent, urgency, confidence (with color: green >= 0.82, yellow >= 0.72, red < 0.72)
    - Recommended next action
    - Handoff reason (if any)
    - Collected fields
    - AI summary
  - **Lead card** (if lead exists): stage pill, service requested, value band, link to lead detail
  - **Task card** (if handoff task exists): type, priority, status, "Mark Done" button
  - **Action bar** (bottom):
    - "Reply" text input → sends via `outbound-actions` (operator message, `sender_type = 'human'`)
    - "Resolve" button → sets conversation `status = 'resolved'`
    - "Escalate" button → creates a new handoff task with custom reason

**Interactions**:
- Click "Mark Done" on a task → `PATCH /v1/tasks/:id` with `status = 'done'`
  - If conversation has no other open tasks, prompt to resolve conversation
- Operator reply → enqueues an `outbound-actions` job with `sender_type = 'human'` (bypasses intelligence worker)
- Real-time: use Supabase Realtime subscription on `conversations` + `messages` tables for the selected business_id to push new messages live

---

### 3. Leads (`/leads`) — Phase 2

**Data source**: `GET /v1/leads?stage=...&limit=50`

**Layout**:
- Kanban or list view (toggle)
- Kanban columns: New | Contacted | Qualified | Awaiting Quote | Quoted | Booked | Won | Lost
- Each card: contact name, service requested, urgency badge, value band, last activity timestamp
- Click card → lead detail modal:
  - All intake fields
  - Linked conversation (link to inbox)
  - Linked quotes (link to quote detail)
  - Linked bookings
  - Activity timeline (from `events` table)
  - Stage history
  - "Move to" dropdown for manual stage changes

---

### 4. Quotes (`/quotes`) — Phase 3

**Data source**: `GET /v1/quotes?status=...&limit=50`

**Layout**:
- List view, grouped by status: Awaiting Review | Sent | Accepted | Declined | Expired
- Each row: contact name, service, amount range, status, created date, sent date
- Click → quote detail:
  - Draft text (editable in Awaiting Review status)
  - Amount low / high (editable)
  - Terms (editable)
  - Linked lead + intake fields summary
  - "Approve & Send" button → `POST /v1/quotes/:id/approve`
  - "Decline" button → `POST /v1/quotes/:id/decline`
  - "Edit" mode for text/amounts before sending
- Status badge colors: awaiting_review=yellow, sent=blue, accepted=green, declined=red, expired=gray

---

### 5. Bookings (`/bookings`) — Phase 4

**Data source**: `GET /v1/bookings?status=...`

**Layout**:
- Calendar view (week/day) + list toggle
- Calendar shows confirmed/tentative bookings as colored blocks
- Each block: contact name, service, time range
- Click → booking detail:
  - Contact info, service, scheduled time, assignee
  - Status: Requested | Tentative | Confirmed | Completed | Cancelled | No-show
  - "Confirm" / "Cancel" / "Reschedule" buttons
  - Link to conversation + lead
  - Google Calendar event link (external)

---

### 6. Reactivation (`/reactivation`) — Phase 5

**Data source**: `GET /v1/reactivation`, `POST /v1/reactivation/preview`, `POST /v1/reactivation/launch`

**Layout**:
- **Launch panel**:
  - Filter controls: stage, service, inactivity period, tags
  - "Preview" button → shows count + sample leads
  - "Launch Campaign" button → confirmation modal → launches batch
- **Campaign history**:
  - List of past batches with: date, total sent, replies, conversions
  - Click → batch detail: per-lead status (sent, replied, converted, no-response)

---

### 7. Knowledge (`/knowledge`) — Phase 2

**Data source**: `GET /v1/knowledge`, `POST /v1/knowledge`, `PATCH /v1/knowledge/:id`

**Layout**:
- List view: title, type badge (FAQ, service, policy, pricing_rule, script, template, other), active/approved toggle, last reviewed date
- Filter by type, active, approved, review_required
- "Add Knowledge" button → form:
  - Title, content (textarea), type (dropdown), tags (multi-select)
  - "Save as Draft" (active=true, approved=false, review_required=true)
  - "Save & Approve" (active=true, approved=true, review_required=false)
- Click item → detail/edit view:
  - Edit title, content, tags
  - Toggle active / approved
  - Show: linked knowledge source (if any), embedding status, last reviewed date
- **Review Queue**: filtered view showing `review_required = true` items
  - "Approve" / "Reject" / "Edit & Approve" buttons

---

### 8. Settings

#### 8a. Business Settings (`/settings/business`) — Phase 1

**Data source**: `GET /v1/businesses/:id`, `PATCH /v1/businesses/:id`

**Fields**:
- Business name, slug
- Vertical (dropdown: appointment, quote, dispatch, repeat, other)
- Timezone (searchable dropdown)
- Hours (weekly schedule builder: per-day open/close time pairs, holiday list)
- Service area (free text or structured address)
- Escalation contacts (email, phone)
- Settings: daily summary enabled, quiet hours override, reactivation stale days

#### 8b. Channels (`/settings/channels`) — Phase 1

**Data source**: `GET /v1/channels`, `POST /v1/channels`, `PATCH /v1/channels/:id`

**Layout**:
- List of channels: type icon, provider, external ID (phone number / email), display name, status badge
- "Add Channel" form: channel_type dropdown, provider, external_id, display_name
- Edit: status toggle (active/paused), display name, config
- Show: webhook URL for Retell configuration (computed from the channel's external_id + service URL)

#### 8c. Services (`/settings/services`) — Phase 3

**Data source**: `GET /v1/services`, `POST /v1/services`, `PATCH /v1/services/:id`

**Layout**:
- List: name, price range, active toggle, required intake fields
- Add/edit form: name, description, base_price_low, base_price_high, required_intake_fields (multi-select from preset list + custom), tags, photo_helpful toggle

#### 8d. Integrations (`/settings/integrations`) — Phase 4

**Layout**:
- Google Calendar: connect/disconnect button, status indicator, selected calendar
- Future: CRM, payment, job management system connectors

#### 8e. Team (`/settings/team`) — Phase 2

**Data source**: Supabase Auth admin + `business_members` table

**Layout**:
- List of team members: name, email, role (owner/admin/operator), joined date
- "Invite Member" form: email, role
- Edit role / remove member

---

## Shared components

| Component | Description | Used in |
|---|---|---|
| `MetricCard` | Number + label + optional sparkline | Dashboard |
| `StatusBadge` | Colored pill for conversation/lead/task/quote/booking status | All list views |
| `UrgencyBadge` | Red/orange/yellow/blue/gray for urgency levels | Inbox, leads |
| `IntentPill` | Small label showing classified intent | Inbox, leads |
| `ConfidenceIndicator` | Colored bar or number (green/yellow/red thresholds) | Inbox detail |
| `MessageBubble` | Chat bubble with direction, sender type, timestamp | Inbox detail |
| `TimeAgo` | Relative timestamp ("2m ago", "1h ago", "Yesterday") | All list views |
| `Pagination` | Offset-based pagination with page size control | All list views |
| `SearchInput` | Debounced search input with clear button | Inbox, leads, knowledge |
| `FilterBar` | Horizontal filter chips or dropdowns | All list views |
| `ConfirmModal` | Confirmation dialog for destructive actions | Resolve, delete, launch |
| `EmptyState` | Illustration + message when no data | All list views |

---

## Real-time updates

Use Supabase Realtime for the following subscriptions (scoped to `business_id`):

| Table | Event | Effect |
|---|---|---|
| `conversations` | UPDATE | Refresh inbox list + detail if selected |
| `messages` | INSERT | Append to message thread in real-time |
| `tasks` | INSERT/UPDATE | Update task cards, badge counts |
| `leads` | INSERT/UPDATE | Refresh lead kanban/list |
| `bookings` | INSERT/UPDATE | Refresh booking calendar |

---

## Theming / branding

- Default: clean white/gray with blue accent (professional, trust-oriented)
- Dark mode: optional, follow system preference
- Business branding (Phase 6): allow business to set primary color + logo for their dashboard instance
- Mobile responsive: sidebar collapses to hamburger menu; conversation detail slides over list on narrow screens
