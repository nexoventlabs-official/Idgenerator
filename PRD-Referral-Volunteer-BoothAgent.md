# Product Requirements Document (PRD)

## PuratchiThaai — Referral System, Volunteer & Booth Agent Management

| Field | Details |
|---|---|
| **Project** | Voter ID Card Generator (PuratchiThaai) |
| **Version** | v5.0 |
| **Date** | March 4, 2026 |
| **Status** | Draft |
| **Tech Stack** | Flask, MongoDB Atlas, Cloudinary, Jinja2, Bootstrap 5, 2Factor.in OTP |
| **Existing DBs** | `voters` (XLSX imports), `generated_voters` (card activity), `generation_stats`, `verified_mobiles`, `otp_sessions` |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Goals & Objectives](#2-goals--objectives)
3. [Feature Overview](#3-feature-overview)
4. [Feature 1 — Referral System (Add New Member)](#4-feature-1--referral-system-add-new-member)
5. [Feature 2 — My Members (Referral Dashboard)](#5-feature-2--my-members-referral-dashboard)
6. [Feature 3 — Become Volunteer](#6-feature-3--become-volunteer)
7. [Feature 4 — Become Booth Agent](#7-feature-4--become-booth-agent)
8. [Feature 5 — Join WhatsApp Channel](#8-feature-5--join-whatsapp-channel)
9. [Admin Panel Enhancements](#9-admin-panel-enhancements)
10. [Database Schema Changes](#10-database-schema-changes)
11. [API Endpoints](#11-api-endpoints)
12. [UI/UX Specifications](#12-uiux-specifications)
13. [Non-Functional Requirements](#13-non-functional-requirements)
14. [Out of Scope](#14-out-of-scope)
15. [Appendix — User Flow Diagrams](#15-appendix--user-flow-diagrams)

---

## 1. Executive Summary

This PRD defines five new features for the PuratchiThaai Voter ID Card Generator platform:

1. **Referral System** — Every voter who generates an ID card gets a unique, permanent referral link (containing their PTC code + a unique Referral ID). They can share this link to refer new unregistered voters.
2. **My Members** — A dashboard showing all voters a user has referred, including basic details.
3. **Volunteer Requests** — Users can request to become a Volunteer via the chatbot; admin approves/rejects.
4. **Booth Agent Requests** — Users can request to become a Booth Agent via the chatbot; admin approves/rejects.
5. **Join WhatsApp Channel** — Direct redirect to the PuratchiThaai WhatsApp channel.

These features enhance the existing chatbot-based ID card generation flow (built in `chatbot.html`) and the Flask admin panel (`/admin`).

---

## 2. Goals & Objectives

| Goal | Metric |
|---|---|
| Grow voter registrations organically through referrals | Track referred member count per voter |
| Give existing voters ownership & a team-building tool | Each voter gets a permanent unique referral link |
| Build a volunteer & booth agent pipeline | Track request → confirmed counts in admin |
| Streamline admin oversight | New admin pages for referrals, volunteer/agent management |
| Drive WhatsApp community engagement | One-tap redirect to WhatsApp channel |

---

## 3. Feature Overview

After a voter successfully generates their ID card (state `S.DONE`), the chatbot currently displays these buttons:

| Button | Feature | Behavior |
|---|---|---|
| 👤+ **Add New Member** | Referral System | Generate & display unique referral link for sharing |
| 👥 **My Members** | Referral Dashboard | Show list of voters referred by this user |
| 🪪 **Become Volunteer** | Volunteer Request | Ask confirmation → send request to admin |
| 🏛️ **Become Booth Agent** | Booth Agent Request | Ask confirmation → send request to admin |
| 📱 **Join WhatsApp Channel** | WhatsApp Redirect | Redirect to WhatsApp channel URL |

---

## 4. Feature 1 — Referral System (Add New Member)

### 4.1 Referral Link Generation

#### Rules

1. Every voter who has generated an ID card is assigned **one permanent, unique referral link**.
2. The referral link format: `{BASE_URL}/refer/{PTC_CODE}/{REFERRAL_ID}`
   - `PTC_CODE` — The voter's existing PTC code (e.g., `PTC-AB12CD3`), already stored in `generated_voters`.
   - `REFERRAL_ID` — A **new unique 8-character alphanumeric ID** (e.g., `REF-XXXXXXXX`), generated once and stored permanently.
3. **Idempotent**: If the voter clicks "Add New Member" again (even after refresh/reload), the **same referral link** must be returned — never generate a new one.
4. The referral link and Referral ID are stored in the `generated_voters` collection alongside the voter's existing record.

#### Referral Link Behavior

When a **new user** opens the referral link:
1. The system records `referred_by_ptc` and `referred_by_referral_id` in the session/URL context.
2. The user is taken to the normal chatbot flow (mobile → OTP → EPIC → photo → generate).
3. **Duplicate check**: If the new user's mobile number or EPIC number is already registered (exists in `generated_voters`), display:  
   > "You have already registered. Only new voters can use this referral link."
4. On successful card generation, the new voter's `generated_voters` document is tagged with:
   - `referred_by_ptc`: The referrer's PTC code.
   - `referred_by_referral_id`: The referrer's Referral ID.
5. The referrer's `referred_members_count` is incremented by 1.

#### API

| Endpoint | Method | Purpose |
|---|---|---|
| `POST /api/chat/get-referral-link` | POST | Body: `{ mobile }` — Returns the voter's existing referral link or generates one if first time. **Always returns the same link for the same voter.** |
| `GET /refer/<ptc_code>/<referral_id>` | GET | Referral landing page. Validates the referral link, stores context, redirects to chatbot with `?ref=<ptc_code>&rid=<referral_id>` query params. |

#### Logic (Backend — `app.py`)

```python
def get_or_create_referral(ptc_code: str) -> dict:
    """
    Returns { referral_id, referral_link } for the voter.
    Creates referral_id ONLY if it doesn't exist yet (idempotent).
    """
    voter = gen_voters_col.find_one({'ptc_code': ptc_code})
    if not voter:
        return None

    if voter.get('referral_id'):
        # Already has one — return it
        return {
            'referral_id': voter['referral_id'],
            'referral_link': f"{config.BASE_URL}/refer/{ptc_code}/{voter['referral_id']}"
        }

    # Generate new unique Referral ID
    chars = string.ascii_uppercase + string.digits
    for _ in range(100):
        rid = 'REF-' + ''.join(random.choices(chars, k=8))
        if not gen_voters_col.find_one({'referral_id': rid}):
            break

    gen_voters_col.update_one(
        {'ptc_code': ptc_code},
        {'$set': {
            'referral_id': rid,
            'referral_link': f"{config.BASE_URL}/refer/{ptc_code}/{rid}",
            'referred_members_count': voter.get('referred_members_count', 0)
        }}
    )
    return {
        'referral_id': rid,
        'referral_link': f"{config.BASE_URL}/refer/{ptc_code}/{rid}"
    }
```

### 4.2 Chatbot UI (Add New Member Button)

When the user clicks **"Add New Member"**:

1. Chatbot calls `POST /api/chat/get-referral-link` with the current mobile number.
2. Bot displays:
   ```
   🔗 Your unique referral link:
   
   [https://yoursite.com/refer/PTC-AB12CD3/REF-XY12AB34]
   
   📋 [Copy Link]  📤 [Share on WhatsApp]
   
   Share this link with new voters. When they register using your link,
   they'll be added to your team!
   ```
3. **Copy Link** button copies to clipboard.
4. **Share on WhatsApp** opens `https://wa.me/?text=...` with pre-filled message including the link.

---

## 5. Feature 2 — My Members (Referral Dashboard)

### 5.1 User-Facing (Chatbot)

When the user clicks **"My Members"**:

1. Chatbot calls `POST /api/chat/my-members` with `{ mobile }`.
2. Backend queries `generated_voters` where `referred_by_ptc == current_voter.ptc_code`.
3. Bot displays:

   **If members exist:**
   ```
   👥 Your Team — 5 Members Referred
   
   ┌──────────────────────────────┐
   │ 1. Rajesh Kumar               │
   │    EPIC: ABC1234567           │
   │    Assembly: Thiruvanmiyur    │
   │    PTC: PTC-XY12345           │
   │    Joined: 01 Mar 2026        │
   ├──────────────────────────────┤
   │ 2. Priya Devi                 │
   │    EPIC: DEF9876543           │
   │    Assembly: Adyar             │
   │    PTC: PTC-MN67890           │
   │    Joined: 28 Feb 2026        │
   └──────────────────────────────┘
   ```

   **If no members:**
   ```
   📭 You haven't referred any members yet.
   
   Click "Add New Member" to get your referral link and start building your team!
   ```

### 5.2 API

| Endpoint | Method | Purpose |
|---|---|---|
| `POST /api/chat/my-members` | POST | Body: `{ mobile }` — Returns list of voters referred by this voter |

#### Response Schema

```json
{
  "success": true,
  "referrer_name": "Tamil Selvan",
  "referrer_ptc": "PTC-AB12CD3",
  "total_referred": 5,
  "members": [
    {
      "name": "Rajesh Kumar",
      "epic_no": "ABC1234567",
      "assembly": "Thiruvanmiyur",
      "district": "Chennai",
      "ptc_code": "PTC-XY12345",
      "generated_at": "2026-03-01T10:30:00Z"
    }
  ]
}
```

---

## 6. Feature 3 — Become Volunteer

### 6.1 User Flow (Chatbot)

When the user clicks **"Become Volunteer"**:

1. Bot asks confirmation:
   ```
   🙋 You want to become a **Volunteer** for PuratchiThaai?
   
   As a volunteer, you'll help us reach more people and organize events in your area.
   
   [✅ Yes, I want to volunteer]  [❌ Cancel]
   ```

2. **If user confirms:**
   - Backend calls `POST /api/chat/request-volunteer` with `{ mobile }`.
   - Checks if request already exists:
     - **Already requested / already confirmed**: "You have already submitted a volunteer request. PuratchiThaai Team will contact you soon! 🙏"
     - **New request**: Creates entry in `volunteer_requests` collection.
   - Bot displays:
     ```
     ✅ Your volunteer request has been submitted!
     
     புரட்சித்தாய் குழு விரைவில் உங்களை தொடர்பு கொள்ளும்.
     PuratchiThaai Team will contact you soon! 🙏
     ```

3. **If user cancels:** Return to DONE state.

### 6.2 Data Model — `volunteer_requests` Collection

```json
{
  "ptc_code": "PTC-AB12CD3",
  "epic_no": "ABC1234567",
  "name": "Tamil Selvan",
  "mobile": "9876543210",
  "assembly": "Thiruvanmiyur",
  "district": "Chennai",
  "photo_url": "https://...",
  "status": "pending",           // pending | confirmed | rejected
  "requested_at": "2026-03-04T10:00:00Z",
  "reviewed_at": null,
  "reviewed_by": "admin"
}
```

### 6.3 API

| Endpoint | Method | Purpose |
|---|---|---|
| `POST /api/chat/request-volunteer` | POST | Body: `{ mobile }` — Submit volunteer request |

---

## 7. Feature 4 — Become Booth Agent

### 7.1 User Flow (Chatbot)

Identical to Volunteer flow but for Booth Agent role.

When the user clicks **"Become Booth Agent"**:

1. Bot asks confirmation:
   ```
   🏛️ You want to become a **Booth Agent** for PuratchiThaai?
   
   As a booth agent, you'll represent our party at your local polling booth.
   
   [✅ Yes, I want to be a Booth Agent]  [❌ Cancel]
   ```

2. **If user confirms:**
   - Backend calls `POST /api/chat/request-booth-agent` with `{ mobile }`.
   - Same duplicate-check logic as volunteers.
   - Bot displays:
     ```
     ✅ Your booth agent request has been submitted!
     
     புரட்சித்தாய் குழு விரைவில் உங்களை தொடர்பு கொள்ளும்.
     PuratchiThaai Team will contact you soon! 🙏
     ```

3. **If user cancels:** Return to DONE state.

### 7.2 Data Model — `booth_agent_requests` Collection

```json
{
  "ptc_code": "PTC-AB12CD3",
  "epic_no": "ABC1234567",
  "name": "Tamil Selvan",
  "mobile": "9876543210",
  "assembly": "Thiruvanmiyur",
  "district": "Chennai",
  "photo_url": "https://...",
  "status": "pending",           // pending | confirmed | rejected
  "requested_at": "2026-03-04T10:00:00Z",
  "reviewed_at": null,
  "reviewed_by": "admin"
}
```

### 7.3 API

| Endpoint | Method | Purpose |
|---|---|---|
| `POST /api/chat/request-booth-agent` | POST | Body: `{ mobile }` — Submit booth agent request |

---

## 8. Feature 5 — Join WhatsApp Channel

### 8.1 Behavior

- **Button**: "Join WhatsApp Channel" (with WhatsApp icon)
- **Action**: `window.open('https://whatsapp.com/channel/XXXXXXXXX', '_blank')`
- The WhatsApp channel URL is stored as an environment variable: `WHATSAPP_CHANNEL_URL`
- Added to `config.py`: `WHATSAPP_CHANNEL_URL = os.getenv("WHATSAPP_CHANNEL_URL", "")`
- A new API endpoint returns the URL so the chatbot JS can use it dynamically:

| Endpoint | Method | Purpose |
|---|---|---|
| `GET /api/whatsapp-channel` | GET | Returns `{ url: "https://..." }` |

### 8.2 Fallback

If `WHATSAPP_CHANNEL_URL` is not set, the button is hidden or displays:
> "WhatsApp channel link will be available soon."

---

## 9. Admin Panel Enhancements

### 9.1 Generated Voters Table — New Columns

Modify the existing `/admin/generated-voters` page and its API to include:

| New Column | Field | Description |
|---|---|---|
| **Referral ID** | `referral_id` | Unique referral ID (e.g., `REF-XY12AB34`) |
| **Referral Link** | `referral_link` | Full referral URL (clickable, copyable) |
| **Referred Members** | `referred_members_count` | Count of voters referred by this voter |

These fields are already stored in the `generated_voters` collection per Feature 1.

### 9.2 Voter Detail Page — Referred Voters List

Modify the existing `/admin/generated-voters/<ptc_code>` (or create new) voter detail page to show:

- All existing voter details (name, EPIC, PTC, mobile, assembly, district, photo, card, etc.)
- **New section: "Referred Voters"** — A table listing all voters where `referred_by_ptc == this_voter.ptc_code`:

| Column | Field |
|---|---|
| # | Row number |
| Name | `name` |
| EPIC No | `epic_no` |
| PTC Code | `ptc_code` |
| Mobile | `mobile` |
| Assembly | `assembly` |
| District | `district` |
| Registered On | `generated_at` |

### 9.3 New Admin Pages — Volunteer Management

#### 9.3.1 Volunteer Requests Page (`/admin/volunteer-requests`)

- **Table columns**: #, Name, PTC Code, EPIC No, Mobile, Assembly, District, Photo, Requested On, Status, Actions
- **Status filter**: Pending / Confirmed / Rejected / All
- **Actions per row**:
  - ✅ **Confirm** → Changes status to `confirmed`, sets `reviewed_at` and `reviewed_by`
  - ❌ **Reject** → Changes status to `rejected`, sets `reviewed_at` and `reviewed_by`
- **Search**: By name, PTC code, EPIC, mobile
- **Pagination**: Same pattern as existing voters list (20 per page)

#### 9.3.2 Confirmed Volunteers Page (`/admin/confirmed-volunteers`)

- Shows only volunteers with `status: "confirmed"`
- **Table columns**: #, Name, PTC Code, EPIC No, Mobile, Assembly, District, Photo, Confirmed On
- **Search & Pagination**: Same as requests page

### 9.4 New Admin Pages — Booth Agent Management

#### 9.4.1 Booth Agent Requests Page (`/admin/booth-agent-requests`)

- Identical layout to Volunteer Requests but for booth agents.
- **Table columns**: #, Name, PTC Code, EPIC No, Mobile, Assembly, District, Photo, Requested On, Status, Actions
- **Actions**: ✅ Confirm / ❌ Reject

#### 9.4.2 Confirmed Booth Agents Page (`/admin/confirmed-booth-agents`)

- Shows only booth agents with `status: "confirmed"`
- **Table columns**: #, Name, PTC Code, EPIC No, Mobile, Assembly, District, Photo, Confirmed On
- **Search & Pagination**: Same pattern

### 9.5 Admin Dashboard — New Stat Cards

Add new stat cards to `/admin` dashboard:

| Stat Card | Icon | Color | Value |
|---|---|---|---|
| Total Referrals | `bi-people-fill` | Teal | Sum of all `referred_members_count` |
| Volunteer Requests | `bi-hand-thumbs-up-fill` | Orange | Count of `volunteer_requests` with `status: pending` |
| Confirmed Volunteers | `bi-person-check-fill` | Green | Count of `volunteer_requests` with `status: confirmed` |
| Booth Agent Requests | `bi-shop` | Purple | Count of `booth_agent_requests` with `status: pending` |
| Confirmed Booth Agents | `bi-building-check` | Blue | Count of `booth_agent_requests` with `status: confirmed` |

### 9.6 Admin Sidebar / Navigation — New Links

Add to the admin layout/navigation:

```
📊 Dashboard
📋 Voters (existing)
📥 Import (existing)
🪪 Generated Voters (existing — enhanced)
─────────────────────
🙋 Volunteer Requests          (NEW)
✅ Confirmed Volunteers         (NEW)
─────────────────────
🏛️ Booth Agent Requests        (NEW)
✅ Confirmed Booth Agents       (NEW)
```

---

## 10. Database Schema Changes

### 10.1 `generated_voters` Collection — New Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `referral_id` | String | `null` | Unique referral ID (`REF-XXXXXXXX`), generated on first "Add New Member" click |
| `referral_link` | String | `null` | Full referral URL |
| `referred_members_count` | Integer | `0` | Number of voters referred by this voter |
| `referred_by_ptc` | String | `null` | PTC code of the voter who referred this voter (null if organic) |
| `referred_by_referral_id` | String | `null` | Referral ID used when this voter was referred |

#### New Indexes

```python
gen_voters_col.create_index('referral_id', unique=True, sparse=True)
gen_voters_col.create_index('referred_by_ptc')
```

### 10.2 New Collection — `volunteer_requests`

```python
volunteer_requests_col = gen_db['volunteer_requests']
volunteer_requests_col.create_index('ptc_code', unique=True)
volunteer_requests_col.create_index('mobile', unique=True)
volunteer_requests_col.create_index('status')
```

| Field | Type | Description |
|---|---|---|
| `ptc_code` | String | Voter's PTC code |
| `epic_no` | String | EPIC number |
| `name` | String | Voter name |
| `mobile` | String | Mobile number |
| `assembly` | String | Assembly constituency |
| `district` | String | District |
| `photo_url` | String | Cloudinary photo URL |
| `status` | String | `pending` / `confirmed` / `rejected` |
| `requested_at` | String (ISO) | Timestamp of request |
| `reviewed_at` | String (ISO) / null | Timestamp of admin action |
| `reviewed_by` | String / null | Admin username |

### 10.3 New Collection — `booth_agent_requests`

Same schema as `volunteer_requests`.

```python
booth_agent_requests_col = gen_db['booth_agent_requests']
booth_agent_requests_col.create_index('ptc_code', unique=True)
booth_agent_requests_col.create_index('mobile', unique=True)
booth_agent_requests_col.create_index('status')
```

---

## 11. API Endpoints

### 11.1 User-Facing (Chatbot) APIs

| # | Endpoint | Method | Body | Response | Auth |
|---|---|---|---|---|---|
| 1 | `/api/chat/get-referral-link` | POST | `{ mobile }` | `{ success, referral_id, referral_link }` | Verified mobile (session) |
| 2 | `/api/chat/my-members` | POST | `{ mobile }` | `{ success, referrer_name, total_referred, members: [...] }` | Verified mobile (session) |
| 3 | `/api/chat/request-volunteer` | POST | `{ mobile }` | `{ success, message, already_requested }` | Verified mobile (session) |
| 4 | `/api/chat/request-booth-agent` | POST | `{ mobile }` | `{ success, message, already_requested }` | Verified mobile (session) |
| 5 | `/api/whatsapp-channel` | GET | — | `{ url }` | Public |
| 6 | `/refer/<ptc_code>/<referral_id>` | GET | — | Redirect to chatbot with ref params | Public |

### 11.2 Admin APIs

| # | Endpoint | Method | Purpose |
|---|---|---|---|
| 7 | `/admin/volunteer-requests` | GET | Page: List volunteer requests |
| 8 | `/admin/api/volunteer-requests` | GET | JSON API: Volunteer requests (search, filter, paginate) |
| 9 | `/admin/api/volunteer-requests/<ptc_code>/confirm` | POST | Confirm a volunteer request |
| 10 | `/admin/api/volunteer-requests/<ptc_code>/reject` | POST | Reject a volunteer request |
| 11 | `/admin/confirmed-volunteers` | GET | Page: Confirmed volunteers |
| 12 | `/admin/booth-agent-requests` | GET | Page: List booth agent requests |
| 13 | `/admin/api/booth-agent-requests` | GET | JSON API: Booth agent requests (search, filter, paginate) |
| 14 | `/admin/api/booth-agent-requests/<ptc_code>/confirm` | POST | Confirm a booth agent request |
| 15 | `/admin/api/booth-agent-requests/<ptc_code>/reject` | POST | Reject a booth agent request |
| 16 | `/admin/confirmed-booth-agents` | GET | Page: Confirmed booth agents |
| 17 | `/admin/generated-voters/<ptc_code>` | GET | Page: Voter detail with referred voters list |

---

## 12. UI/UX Specifications

### 12.1 Chatbot Buttons (Post Card Generation — `S.DONE` State)

The five buttons are displayed as WhatsApp-style vertical action buttons (already partially implemented in `chatbot.html`):

```html
<div class="vertical-actions">
  <button class="btn-reply" onclick="handleAddMember()">
    <i class="bi bi-person-plus-fill me-1"></i> Add New Member
  </button>
  <button class="btn-reply" onclick="handleMyMembers()">
    <i class="bi bi-people-fill me-1"></i> My Members
  </button>
  <button class="btn-reply" onclick="handleBecomeVolunteer()">
    <i class="bi bi-person-badge-fill me-1"></i> Become Volunteer
  </button>
  <button class="btn-reply" onclick="handleBecomeBoothAgent()">
    <i class="bi bi-shop me-1"></i> Become Booth Agent
  </button>
  <a href="#" class="btn-reply" onclick="handleWhatsAppChannel()" style="text-decoration:none;">
    <i class="bi bi-whatsapp me-1"></i> Join WhatsApp Channel
  </a>
</div>
```

### 12.2 Referral Link Display (Chatbot Bubble)

```
🔗 Your Referral Link

┌─────────────────────────────────────────┐
│ https://yoursite.com/refer/PTC-AB12.../REF-XY... │
└─────────────────────────────────────────┘

[📋 Copy Link]  [📤 Share via WhatsApp]

Share with new voters to grow your team! 🚀
```

### 12.3 My Members Display (Chatbot Bubble)

Cards-style layout within the chat bubble showing member name, EPIC, assembly, and join date. Maximum 10 latest shown; for more, a "View All" link opens a dedicated page.

### 12.4 Admin Table Design

Follow the existing admin table pattern from `generated_voters.html`:
- Bootstrap 5 table with `table-hover`
- Search bar at top
- Pagination at bottom
- Status badges: `pending` = yellow, `confirmed` = green, `rejected` = red
- Action buttons: Small bootstrap buttons (outline-success for confirm, outline-danger for reject)

### 12.5 Localization (Tamil / English)

All new chatbot messages must be added to both `i18n.en` and `i18n.ta` objects in `chatbot.html`:

```javascript
// New i18n keys to add:
{
  // Referral
  add_member_title: '🔗 Your Referral Link',
  add_member_copy: '📋 Copy Link',
  add_member_share: '📤 Share via WhatsApp',
  add_member_desc: 'Share with new voters to grow your team!',
  already_registered: 'This voter is already registered. Only new voters can use referral links.',
  referral_success: '✅ New member registered through your referral!',

  // My Members
  my_members_title: '👥 Your Team',
  my_members_count: '{count} Members Referred',
  my_members_empty: '📭 You haven\'t referred any members yet.',
  my_members_empty_cta: 'Click "Add New Member" to get your referral link!',

  // Volunteer
  volunteer_ask: '🙋 You want to become a **Volunteer** for PuratchiThaai?',
  volunteer_desc: 'As a volunteer, you\'ll help us reach more people and organize events.',
  volunteer_confirm_btn: '✅ Yes, I want to volunteer',
  volunteer_cancel_btn: '❌ Cancel',
  volunteer_submitted: '✅ Your volunteer request has been submitted!',
  volunteer_contact: 'PuratchiThaai Team will contact you soon! 🙏',
  volunteer_already: 'You have already submitted a volunteer request.',

  // Booth Agent
  booth_agent_ask: '🏛️ You want to become a **Booth Agent** for PuratchiThaai?',
  booth_agent_desc: 'As a booth agent, you\'ll represent our party at your local polling booth.',
  booth_agent_confirm_btn: '✅ Yes, I want to be a Booth Agent',
  booth_agent_cancel_btn: '❌ Cancel',
  booth_agent_submitted: '✅ Your booth agent request has been submitted!',
  booth_agent_contact: 'PuratchiThaai Team will contact you soon! 🙏',
  booth_agent_already: 'You have already submitted a booth agent request.',

  // WhatsApp
  whatsapp_unavailable: 'WhatsApp channel link will be available soon.'
}
```

---

## 13. Non-Functional Requirements

| Requirement | Details |
|---|---|
| **Idempotency** | Referral link generation must be idempotent — same link on every click |
| **Uniqueness** | `referral_id` must be globally unique across all voters (enforced by MongoDB unique sparse index) |
| **Performance** | My Members query must be indexed on `referred_by_ptc` for fast lookups |
| **Security** | Volunteer/Booth Agent requests require a verified mobile (OTP verified in current session) |
| **Duplicate Prevention** | One volunteer request per voter (unique on `ptc_code`), one booth agent request per voter |
| **Admin Auth** | All admin endpoints remain behind `require_admin_login` guard |
| **Mobile Responsive** | All new admin pages must work on mobile (existing Bootstrap 5 responsive patterns) |
| **Backward Compatibility** | Existing voters without referral fields should continue to work (all new fields are optional/nullable) |

---

## 14. Out of Scope

- SMS/WhatsApp notifications when volunteer/booth agent requests are approved
- Multi-level referral tracking (only direct referrals tracked)
- Referral rewards or gamification
- Admin editing of referral links
- Volunteer/Booth Agent role-based access in the platform
- WhatsApp Bot integration (current scope is web chatbot only)

---

## 15. Appendix — User Flow Diagrams

### 15.1 Referral Flow

```
┌──────────────────────────────────┐
│ Voter generates ID card (DONE)   │
│ → Clicks "Add New Member"        │
└───────────────┬──────────────────┘
                │
                ▼
┌──────────────────────────────────┐
│ POST /api/chat/get-referral-link │
│ (with mobile number)             │
└───────────────┬──────────────────┘
                │
        ┌───────┴───────┐
        ▼               ▼
   Has referral_id?  No referral_id?
        │               │
        │               ▼
        │      Generate REF-XXXXXXXX
        │      Store in generated_voters
        │               │
        ▼               ▼
   Return existing    Return new
   referral link      referral link
        │               │
        └───────┬───────┘
                ▼
┌──────────────────────────────────┐
│ Display link in chatbot bubble   │
│ [Copy] [Share via WhatsApp]      │
└──────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────┐
│ New voter opens referral link    │
│ GET /refer/<ptc>/<ref_id>        │
│ → Redirect to /?ref=PTC&rid=REF │
└───────────────┬──────────────────┘
                │
                ▼
┌──────────────────────────────────┐
│ Normal chatbot flow              │
│ Mobile → OTP → EPIC → Photo     │
│                                  │
│ CHECK: Already registered?       │
│ → Yes: Block + show message      │
│ → No:  Continue generation       │
└───────────────┬──────────────────┘
                │
                ▼
┌──────────────────────────────────┐
│ Card generated successfully      │
│ Tag: referred_by_ptc = referrer  │
│ Increment referrer's count       │
└──────────────────────────────────┘
```

### 15.2 Volunteer / Booth Agent Flow

```
┌──────────────────────────────────┐
│ Voter in DONE state              │
│ → Clicks "Become Volunteer"      │
│   or "Become Booth Agent"        │
└───────────────┬──────────────────┘
                │
                ▼
┌──────────────────────────────────┐
│ Bot: "Do you want to become a    │
│ Volunteer/Booth Agent?"          │
│ [✅ Confirm] [❌ Cancel]          │
└───────────────┬──────────────────┘
                │
        ┌───────┴───────┐
        ▼               ▼
    Confirm           Cancel
        │               │
        ▼               ▼
   POST request     Return to
   to backend       DONE state
        │
        ▼
┌──────────────────────────────────┐
│ Already requested?               │
│ → Yes: "Already submitted" msg   │
│ → No:  Create request (pending)  │
│        "Team will contact you"   │
└──────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────┐
│ Admin Panel                      │
│ → Volunteer/Agent Requests page  │
│ → Admin clicks Confirm/Reject    │
│ → Moves to Confirmed page        │
└──────────────────────────────────┘
```

### 15.3 Admin Pages Hierarchy

```
/admin
├── /                           Dashboard (enhanced with new stats)
├── /voters                     Imported voters list
├── /voters/<epic_no>           Voter detail
├── /import                     Import XLSX/CSV
├── /generated-voters           Generated voters (enhanced with referral columns)
├── /generated-voters/<ptc>     Voter detail + Referred voters list  ← NEW
├── /volunteer-requests         Pending/All volunteer requests       ← NEW
├── /confirmed-volunteers       Confirmed volunteers                 ← NEW
├── /booth-agent-requests       Pending/All booth agent requests     ← NEW
└── /confirmed-booth-agents     Confirmed booth agents               ← NEW
```

---

## Files to Modify

| File | Changes |
|---|---|
| `config.py` | Add `WHATSAPP_CHANNEL_URL` env var |
| `app.py` | Add new collections, indexes, API routes, admin routes, helper functions |
| `templates/user/chatbot.html` | Wire up 5 buttons with JS handlers, add i18n keys, referral link display, my members display, volunteer/agent confirmation flow |
| `templates/admin/generated_voters.html` | Add Referral ID, Referral Link, Referred Count columns |
| `templates/admin/dashboard.html` | Add new stat cards for referrals, volunteers, booth agents |
| `templates/base.html` | Add new admin sidebar links |
| `templates/admin/volunteer_requests.html` | **NEW** — Volunteer requests list with confirm/reject |
| `templates/admin/confirmed_volunteers.html` | **NEW** — Confirmed volunteers list |
| `templates/admin/booth_agent_requests.html` | **NEW** — Booth agent requests list with confirm/reject |
| `templates/admin/confirmed_booth_agents.html` | **NEW** — Confirmed booth agents list |
| `templates/admin/generated_voter_detail.html` | **NEW** — Voter detail page with referred voters list |

---

## Acceptance Criteria

### Referral System
- [ ] Clicking "Add New Member" generates a unique referral link containing PTC code + Referral ID
- [ ] Clicking "Add New Member" again (even after page refresh) returns the **same** referral link
- [ ] Referral link can be copied to clipboard
- [ ] Referral link can be shared via WhatsApp
- [ ] Opening a referral link starts the chatbot with referral context
- [ ] Already registered users (by mobile or EPIC) are blocked from using referral links
- [ ] Successful registration via referral tags the new voter with `referred_by_ptc` and `referred_by_referral_id`
- [ ] Referrer's `referred_members_count` increments on successful referral

### My Members
- [ ] Clicking "My Members" shows a list of all referred voters with basic details
- [ ] Shows "no members yet" message if none referred
- [ ] Displays total count and individual member details

### Volunteer/Booth Agent
- [ ] Clicking "Become Volunteer" asks for confirmation before submitting
- [ ] On confirmation, creates a `pending` request in `volunteer_requests`
- [ ] Shows "PuratchiThaai Team will contact you" message after submission
- [ ] Prevents duplicate requests (same voter cannot request twice)
- [ ] Same flow works for "Become Booth Agent" with `booth_agent_requests`

### WhatsApp Channel
- [ ] Clicking "Join WhatsApp Channel" redirects to the configured WhatsApp URL
- [ ] URL is configurable via `WHATSAPP_CHANNEL_URL` environment variable

### Admin Panel
- [ ] Generated Voters table shows new columns: Referral ID, Referral Link, Referred Members Count
- [ ] Voter detail page shows list of voters referred by this voter
- [ ] Volunteer Requests page shows all requests with Confirm/Reject actions
- [ ] Confirmed Volunteers page shows only confirmed volunteers
- [ ] Booth Agent Requests page shows all requests with Confirm/Reject actions
- [ ] Confirmed Booth Agents page shows only confirmed booth agents
- [ ] Dashboard shows new stat cards for referrals, volunteers, booth agents
- [ ] Admin sidebar includes links to all new pages
- [ ] All new admin pages support search and pagination

---

*End of PRD*
