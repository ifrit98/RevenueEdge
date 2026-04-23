# Revenue Edge Dashboard

Operator-facing UI for managing the Revenue Edge agent. Built with Next.js 16, React 19, Supabase SSR auth, and Tailwind CSS.

## Tech Stack

- **Next.js 16** with App Router
- **React 19** with Server Components
- **Supabase** for authentication (email/password via `@supabase/ssr`)
- **Tailwind CSS** for styling
- **Recharts** for metrics charts
- **Lucide React** for icons

## Pages

| Route | Purpose |
|-------|---------|
| `/` | Dashboard home: today's metrics (missed calls, recovered leads, quotes, bookings, revenue) with charts |
| `/inbox` | Task inbox: human handoffs, callbacks, quote reviews, knowledge reviews with filters |
| `/leads` | Lead pipeline: list with stage badges, urgency, value, and inline stage updates |
| `/quotes` | Quote management: list with approve/decline actions |
| `/bookings` | Booking list: upcoming/completed/cancelled with status badges |
| `/knowledge` | Knowledge base: list, create, edit, delete items; bulk ingestion (website, document, Google Docs) |
| `/reactivation` | Stale lead reactivation: preview segment, launch campaign, batch status |
| `/settings/business` | Business profile: name, vertical, timezone, hours |
| `/settings/channels` | Channel configuration: phone, SMS, email, web |
| `/settings/services` | Service catalog: create/edit services with pricing and intake fields |
| `/settings/integrations` | Third-party connections: Google Calendar OAuth |
| `/login` | Email/password login |
| `/signup` | New user registration |

## Local Development

### Prerequisites

- Node.js 20+
- A running `re-api` instance (default: `http://localhost:8080`)
- A Supabase project

### Setup

```bash
cd apps/dashboard
npm install
```

Create `.env.local`:

```
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
NEXT_PUBLIC_API_URL=http://localhost:8080
```

### Run

```bash
npm run dev
# Open http://localhost:3000
```

### Build

```bash
npm run build   # Production build (standalone output)
npm start       # Serve production build
```

## Authentication Flow

1. User submits email/password on `/login` or `/signup`
2. Supabase Auth returns a session with JWT
3. Next.js middleware (`src/middleware.ts`) refreshes session cookies on every request
4. Unauthenticated users are redirected to `/login`; authenticated users on `/login` are redirected to `/`
5. API calls use `apiFetch()` from `src/lib/api.ts`, which attaches `Authorization: Bearer <token>` and `x-business-id` headers
6. The user must be a member of a business (in `business_members`) to see any data

## Project Structure

```
src/
├── app/
│   ├── (app)/           # Authenticated pages (layout with sidebar)
│   │   ├── page.tsx     # Dashboard home
│   │   ├── inbox/
│   │   ├── leads/
│   │   ├── quotes/
│   │   ├── bookings/
│   │   ├── knowledge/
│   │   ├── reactivation/
│   │   └── settings/
│   │       ├── business/
│   │       ├── channels/
│   │       ├── services/
│   │       └── integrations/
│   ├── login/
│   ├── signup/
│   ├── layout.tsx       # Root layout (globals, fonts)
│   └── globals.css      # Tailwind base + custom styles
├── components/
│   └── StatusBadge.tsx  # Reusable status badge component
├── lib/
│   ├── api.ts           # API client (apiFetch)
│   ├── format.ts        # Date/currency/text formatters
│   └── supabase/
│       ├── client.ts    # Browser Supabase client
│       ├── server.ts    # Server Supabase client
│       └── middleware.ts # Session refresh + auth redirects
└── middleware.ts         # Next.js middleware (delegates to supabase/middleware)
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Yes | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Yes | Supabase anonymous key |
| `NEXT_PUBLIC_API_URL` | No | API base URL (default: `http://localhost:8080`) |

In production, these are passed as Docker build args (see `docker-compose.prod.yml`).
