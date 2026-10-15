# Sabraah — How to use

Speak or type in **English**. Sabraah answers in English.

You do **not** need option cards for goals. Say the trip in a normal sentence. Sabraah fills WHO / WHERE / WHEN / WHY / HOW from what you already said, and only asks what is still missing.

This page is the **practise script** for the AI Pointers questionnaire (flights, events, event venues). Copy the **You say** lines.

---

## Start here (do this, then speak)

Use Super Travel in the browser. Look at this page, then say the **English line** in the table.

### 1. Open the site

1. Start the stack: **api-repository :8002** → **sabrah-ai :8000** → **super-travel-web**.
2. Open Super Travel **home** on the port `npm start` printed (`3000` if free, otherwise `3004` while CallAgent is on `3000`): `http://localhost:3004/home`
3. **Log in** (needed for events, venues, and booking pages).
4. Bottom-right **Sabraah orb** — click it.
5. Allow the **microphone**.
6. Turn **Always listen for “Sabraah”** on (first time: tap **Yes, always listen**).

### 2. Wake her

Say this and **wait** until she answers. Do not dump the trip in the same breath as the wake word.

| You say | What happens |
|---------|----------------|
| `Hey Sabraah` | She wakes and greets you. Then you speak the trip. |

If always-listen is off: click the orb (or Talk), then speak.

You can also **type** in the box and press Send.

### 3. What to say next (copy these)

Pick **one** line after she greets you.

| What you want | Say this (English) |
|---------------|-------------------|
| City + date + any event + trains | `I want to go to Jaipur on 19 October. Is there any event?` |
| Same, with origin already | `Pune to Jaipur on 19 October, is there any event?` |
| Events only | `What events are on` or `Book an event` |
| Events in a city | `Events in Mumbai` or `Zakir Khan events in Jaipur` |
| Artist | `I want an event for Zakir Khan. Do you have any events in the list?` |
| Concert / comedy / festival | `Concerts in Mumbai` or `Comedy shows in Jaipur` |
| Trains only | `Book a train from Pune to Delhi tomorrow, two passengers` |
| Flights only | `Book a flight from Indore to Delhi tomorrow, one passenger, economy` |
| Hotel | `Book a hotel in Goa from 20 October, two nights, two guests` |
| Event venue | `I want to book an event venue` or `Wedding venue in Jaipur for 200 guests` |

---

## AI Pointers — practise these (your questionnaire)

Wake first: `Hey Sabraah` → wait for the greeting → then one of the lines below.

### A. Flights

**Flight details on screen:** After search, cards in the orb should show **name, cabin class, times, price**, with space between options. If they look cramped, refresh Super Travel so the latest floater CSS loads.

**She speaks too fast:** After options appear, say `read them slowly`. She pauses with **Next.** between Option 1, 2, 3. Cabin class is spoken with each option (economy / premium economy / business — from Super Travel, not invented).

**Travel preferences she should capture** (you can dump these in one sentence):

| Pointer | You say | She should |
|---------|---------|------------|
| Where do you want to travel? | `Indore to Delhi` | Fill origin + destination. Do not treat `via flight` as a city |
| Purpose of travel | `leisure` / `family` / `business` / `wedding` / `medical` / `emergency` / `other` | Ask only if you did not say it |
| Preferred travel class | `economy` / `premium economy` / `business class` | Ask before search if missing. This class is **stored and searched** |
| Medical / travel needs | `wheelchair assistance` / `I need oxygen` / `medical condition, prefer a direct flight` | Mark assistance, prefer direct, ask **what happened** if purpose is medical |

**Practise flight (copy this):**

1. `Hey Sabraah` *(wait)*
2. `Book a flight`
3. `Indore to Delhi` — WHERE
4. `Tomorrow` — WHEN
5. `Leisure` (or `medical` / `emergency`) — WHY
6. `One passenger` — WHO
7. `Economy` — class
8. Look at the **spaced cards**, then `Read them slowly`
9. `Option 1`

**Medical:** `Book a flight from Pune to Delhi tomorrow, one passenger, medical.` → she asks what happened → `Surgery for my father, wheelchair, direct flight please.`

**Emergency:** `Book a flight from Indore to Delhi tomorrow, one passenger, emergency.` → she shows **earliest and fastest**, not cheapest.

### B. Events (tickets, not venues)

Email and mobile come from the **logged-in Super Travel profile**. She should **not** re-ask email/phone if they are already on the account. She still asks **full name, gender, date of birth**.

**Practise event:**

1. `Hey Sabraah` *(wait)*
2. `Concerts in Mumbai` or `I want to go to Jaipur on 19 October. Is there any event?`
3. `Tell me about` + the event name
4. `Yes, book this`
5. `Rahul Sharma`
6. Gender + date of birth (skip email/phone if profile has them)
7. Booking page opens — click **Book / Pay**

### C. Event venues (RFP — not concert tickets)

She asks only what you have not already said, in this order:

| She asks | You say |
|----------|---------|
| What is the event you want to book an event venue for? | `Wedding` / `birthday` / `corporate` / `conference` / `something else` |
| If you said **something else**: she asks what the event is, in your words | You say it — `comedy night`, `concert`, `baby shower`, `pooja`. Live shows → **events API**. Private occasions → **venues**. |
| What is the guest count? | `200 guests` |
| Which location are you looking to do your event in? | `Jaipur` |
| What date? (for the form) | `19 October` or `skip` |
| Would you prefer a hotel or a banquet or a government venue? | `Hotel` / `banquet` / `government` / `no preference` |
| Would you also need food, decoration, etc.? We can recommend event companies. | `Food and decoration` / `food` / `decoration` / `just the venue` |
| If you have a particular budget, we would like to know. We can send RFPs. | `Budget 5 lakh` or `skip` |

Then she **redirects**: venue list + RFP page. You submit the RFP on the site. Voice does **not** POST RFPs or charge a card. Food/decoration go in **additional requirements** (there is no separate event-company listing page). Proposals: venue booking confirm and **Profile → RFPs**.

**One breath:** `Wedding venue in Jaipur for 200 guests, banquet, food and decoration, budget 5 lakh.`

**Step by step:** `I want to book an event venue` then answer one question at a time from the table above.

### 4. Event + train combo (after the first line)

| Step | You say | Why |
|------|---------|-----|
| 1 | `I want to go to Jaipur on 19 October. Is there any event?` | She lists events in Jaipur and asks where you are travelling from |
| 2 | `From Pune` | She searches trains Pune → Jaipur on that date |
| 3 | `Tell me about` + event name | Event details |
| 4 | `Yes, book this` | Start booking |
| 5 | Guest **full names** | Example: `Rahul Sharma and Priya Verma` |
| 6 | Each guest: **gender, date of birth**, plus **email and 10-digit phone** if they are not already on your Super Travel profile | She asks only missing fields |
| 7 | Booking page opens — click **Book / Pay** | Voice does not take payment |
| Or | `Option 1` after trains are on screen | Pick a train instead of (or after) the event |

If she finds no event, she still offers trains.

### 5. Event venue (RFP)

| Step | You say | Why |
|------|---------|-----|
| 1 | `I want to book an event venue` | She asks what the event is for |
| 2 | `Wedding` | Event type |
| 3 | `200 guests` | Guest count |
| 4 | `Jaipur` | Location |
| 5 | `19 October` or `skip` | Event date for the form |
| 6 | `Banquet` | Hotel / banquet / government, or `no preference` |
| 7 | `Food and decoration` | Event-company services, or `just the venue` |
| 8 | `Budget 5 lakh` or `skip` | Optional budget |
| 9 | Venue list opens — say a venue name | Opens that venue's RFP page |
| 10 | Submit the RFP on the page | Voice does not send RFPs or take payment |

One breath also works: `Wedding venue in Jaipur for 200 guests, banquet, food and decoration, budget 5 lakh`.

Food, decoration, and other event-company needs go in **additional requirements** on the RFP. After you submit, proposals are on **venue booking confirm** and **Profile → RFPs**.

---

## Where to open it

| App | URL | Use for |
|-----|-----|---------|
| Super Travel site widget (orb) | `http://localhost:3004/home` (or `3000` if that port is free) | Main product: talk on the website |
| Sabraah AI voice UI | `http://127.0.0.1:8000` | Standalone voice page |
| Admin / agents | `http://127.0.0.1:8000/admin` | Prompt and first-message edits |

Always-listen: say **Hey Sabraah**. Or type in the box and press Send. Or press **Talk**.

---

## What Sabraah is deciding

Every conversation feeds this model. You can answer several in one sentence.

| Question | What it means | Example you can say |
|----------|----------------|---------------------|
| **WHERE?** | From → to | `Indore to Delhi` / `Pune to Mumbai` |
| **WHEN?** | Travel date | `tomorrow` / `20 September 2026` / `flexible dates` |
| **WHY?** | Purpose (better recommendations) | `leisure` / `family` / `business` / `honeymoon` / `medical` / `emergency` / `other` |
| **WHO?** | Who is travelling | `just me` / `with my wife` / `two adults and one child` / `family` |
| **HOW?** | Flight, train, hotel, event, or venue | `book a flight` / `train` / `hotel in Goa` / `book an event` / `book an event venue` |
| **HOW MUCH?** | Budget if you have one | `budget under 50000` / `2 lakh` |
| **WHAT THEY VALUE** | Price vs time vs comfort | `cheapest` / `fastest` / `cheap but not uncomfortable` / `direct flight` |
| **WHAT THEY NEED** | Restrictions | `vegetarian` / `no 4 AM flights` / `wheelchair` / `medical condition` / `relaxed itinerary` |

Do **not** wait for a form. Example:

> I want to take my family from Indore to Delhi tomorrow, two adults one kid, cheapest direct flight.

Sabraah should search flights, not trains, and not ask those facts again.

---

## Your 31-module questionnaire (what is live)

This is the SABRAAH decision model you wrote. **She asks the next missing slot only** — not Q1–Q31 like a form.

**Starting trip discovery (say only `Book a flight` or `Book a train` to hear these in order):**

1. **WHERE** — Where would you like to go?
2. **WHEN** — When do you want to travel? Fixed or flexible?
3. **WHY** — Purpose: leisure, family, business, honeymoon, medical, emergency, or something else?
4. **WHO** — How many adults / children / just me?
5. Then **HOW** extras: cabin class (flights), medical follow-up if needed. Budget and cheapest/fastest if you mention them.

| Module from your doc | Live on Super Travel voice? |
|----------------------|-----------------------------|
| Customer profile (name, email, phone — do not re-ask) | Yes, if logged in |
| Trip discovery (Where → When → Why → Who) | Yes |
| Destination intelligence (cheapest vs fastest vs balanced) | Partial — she recommends using your priority |
| Flights | Yes |
| Hotels | Yes (city, dates, guests) |
| Trains | Yes |
| Events & experiences | Yes |
| Event venues / RFPs | Yes |
| Group / full coach / charter | Yes (>9 people) |
| Amendments & cancellation / refund | Yes (PNR) |
| Natural language / intent | Yes (cheap but not uncomfortable, no 4 AM, wheelchair, …) |
| Comparison & recommendation | Yes after search |
| Booking confirmation (open Super Travel page) | Yes — voice does not take payment |
| Buses, cruises, car rental, airport transfers | Not live yet |
| Holiday packages, itinerary builder, multi-city builder | Not live yet |
| Visa, insurance, wallet, loyalty | Not live yet |
| Pre-travel / during-travel / post-trip companion | Not live yet |
| Human escalation | Partial (cancel/refund / talk to human wording) |

If you ask a not-live module, she says it is not live yet and offers flights / trains / hotels / events / venues.

---

## Live vs not live

| Live now (use these) | Not live yet (Sabraah will say so) |
|----------------------|------------------------------------|
| Flights | Buses |
| Trains | Cruises |
| Hotels | Car rental / chauffeur |
| Events | Visa |
| Cancel / refund | Travel insurance |
| Group / full coach (more than 9 people) | Wallet, loyalty, holiday packages, full itinerary builder |

---

## 1. Start / greeting

**For:** waking the assistant.

**How:**

1. Say `Hey Sabraah`
2. Or type `Hi Sabraah` and Send

Sabraah invites the trip. Then either dump the trip in one line, or pick a module below.

---

## 2. Flights

**For:** plane tickets on Super Travel (search → pick → fare/extras → checkout page). Card is **not** charged in voice.

**How — shortest path:**

| Step | You say | Why |
|------|---------|-----|
| 1 | `I want to book a flight` | Sets HOW = flight |
| 2 | `Indore to Delhi` | WHERE |
| 3 | `Tomorrow` or `20 September 2026` | WHEN |
| 4 | `Leisure` / `business` / `honeymoon` / `medical` / `emergency` / `other` | WHY. Medical → she asks what happened. Emergency → earliest and fastest |
| 5 | `One passenger` / `two adults and one child` | WHO |
| 6 | `Economy` / `premium economy` / `business class` / `skip` | Cabin class — searched on Super Travel |
| 7 | Screen shows flights (class, times, price, spaced cards) → `Option 1` | Pick the flight |
| 8 | `Saver` / `Flexi` / `Super`, or `skip` | Fare type — only if that airline returned more than one |
| 9 | `Veg meal` / `no meal` | Paid meals — only if that fare has them |
| 10 | `5 kg extra bag` / `skip` | Extra baggage — only if offered on that fare |
| 11 | `Priority check-in` / `skip` | Only if that fare has priority check-in |
| 12 | Guest **full name**, then **gender** and **date of birth** | Email and 10-digit phone are skipped if your Super Travel profile already has them |
| 13 | Passenger details page (`/flight-details`) opens prefilled — continue there to pay | Voice does not take payment |

Sabraah asks **only** extras that Super Travel actually returned for that fare (same list as Fare Details / Extra Services). If a fare has no meals or bags, she will not invent them. She does **not** open `/preview/flights/review`.

**Natural one-liners (preferred):**

- `Book a flight from Indore to Delhi tomorrow for 1 passenger`
- `Delhi to Dubai, round trip, 12 October, 2 adults, cheapest`
- `Direct morning flight Pune to Bangalore, business class`
- `Cheap but not uncomfortable, and I don't want to wake up at 4 AM`
- `Wheelchair assistance, prefer a direct flight`

**Preferences you can add anytime:**

| You say | Sabraah understands |
|---------|---------------------|
| `cheapest` / `budget` | Value = price |
| `fastest` / `shortest` | Value = time |
| `direct` / `non-stop` | Direct only |
| `economy` / `premium economy` / `business class` | Cabin (this is sent on search) |
| `wheelchair` / `medical condition` / `oxygen` | Assistance; prefers easier / direct connections |
| `round trip` / `one way` / `multi city` | Trip type |

After search she recommends **one** option, names the **cabin class**, then asks: **Shall I read them out slowly, or will you pick from the screen?**

- Say `read them` / `read` — she speaks the numbered list **slowly** (pauses between options) **and** the recommendation.
- Say `I'll pick from the screen` — she keeps the recommendation and waits for `Option 1`.

---

## 3. Trains

**For:** train search, pick a train, meal, passenger names, phone, confirm booking.

**How:**

| Step | You say | Why |
|------|---------|-----|
| 1 | `Book a train` | HOW = train |
| 2 | `Pune to Delhi` | WHERE |
| 3 | Date | WHEN |
| 4 | Purpose (`leisure` / `personal work` / `wedding` / `medical` / `emergency` / `other`) | WHY. Medical → what happened. Emergency → earliest and fastest |
| 5 | `Two passengers` | WHO |
| 6 | `read them` or `I'll pick from the screen` | She asks first; if you say read, she speaks options + a recommendation |
| 7 | `Option 1` or train name | Pick |
| 8 | `Veg` / `Non veg` / `Jain` / `No meal` | Meal once |
| 9 | `Rahul, Priya` | Names |
| 10 | `9876543210` | Phone |
| 11 | `Yes` | Confirm |

**Also useful:** `cheapest train` / `fastest` / `AC` / seats together is remembered as preference.

**Group:** more than 9 people → she steers to **full coach / charter**, not two bookings of 9.

---

## 4. Hotels

**For:** hotel search in a city (Super Travel hotel API).

**How:**

| Step | You say | Why |
|------|---------|-----|
| 1 | `Book a hotel` / `need a stay in Goa` | HOW = hotel |
| 2 | City if not already known | WHERE |
| 3 | Check-in date, nights or check-out | WHEN |
| 4 | Guests | WHO |
| 5 | `Option 1` | Pick from screen |

If the trip already has a destination and dates (from a flight), you can say `add a hotel` and she should reuse them.

---

## 5. Events

**For:** concerts, comedy, festivals, exhibitions, conferences, sports, theme parks, attractions — then open the booking page with guest details. Voice does **not** take payment. If you also name a city and date, she suggests **trains** to get there. If a trip is already in progress, she asks: **Would you like me to add this event to your existing trip?**

**How — events only:**

| Step | You say | Why |
|------|---------|-----|
| 1 | `Book an event` / `concerts in Mumbai` / `what events are on` | If you did not name a city, she asks **which city** |
| 2 | `Mumbai` / `Jaipur` | Location of the event |
| 2 | Listen to Option 1, 2, … | She speaks names slowly |
| 3 | `Tell me about` + the event name | Details |
| 4 | `Yes, book this` | Confirm intent |
| 5 | Guest **full names** | Example: `Rahul Sharma` |
| 6 | **Gender** and **date of birth** (email / phone skipped if already on your profile) | Required before the page opens |
| 7 | Booking page opens — click **Book / Pay** | Checkout |

**How — city + date + event + trains:** see **Start here → 4** at the top. After trains are on screen, say the **event name** to book tickets, or `Option 1` to pick a train.

If a guest field is wrong after the page opens, keep talking: `email is wrong, it is rahul@gmail.com`.

---

## 6. Cancel or refund

**For:** an existing booking / PNR.

**How:**

| Step | You say |
|------|---------|
| 1 | `Cancel a ticket` or `Request a refund` |
| 2 | PNR like `BK-XXXXXXXX` |
| 3 | `Yes` to confirm |

She will not invent refund amounts.

---

## 7. Group / corporate / full coach

**For:** large groups, wedding parties, entire coach / train charter.

**How:** say any of:

- `Full coach for 40 people, Pune to Delhi, 20 September`
- `Can we book the entire train?`
- `Group of 150, we need buses` (buses not live — she will say so and take a group/charter path where supported)

---

## 8. Trip discovery (no module picked)

**For:** you know the trip but not “flight vs train” yet.

**How:**

> I want to go from Jaipur to Mumbai next Friday with my wife.

Sabraah should ask: **fly, train, or hotel?**  
International places (Dubai, Europe, Switzerland, Paris, …) default to **flight**.

---

## 9. What if / compare / recommend

After options are on screen you can say:

- `read them` — she reads the numbered list and recommends one
- `I'll pick from the screen` — she recommends one and waits
- `Option 2`
- `the cheapest one`
- `the fastest one`
- `what if I leave one day earlier` (she should reuse the trip and change the date)

She compares using **your** priority (price / time / comfort), not a generic “best”.

---

## 10. Corrections (do not restart)

| Problem | You say |
|---------|---------|
| Wrong name | `Second person's name is wrong, it is Amit` |
| Wrong date | `Change the date to 22 September` |
| Wrong city | `Destination is Jaipur not Delhi` |
| Repeat details | `What do you have so far?` |

---

## Example conversations

### Event + trains (city and date)

**You:** Hey Sabraah  
*(wait for the greeting)*  
**You:** I want to go to Jaipur on 19 October. Is there any event?  
**Sabraah:** Event names, then asks which city you are travelling from.  
**You:** From Pune  
**Sabraah:** Trains Pune to Jaipur on the screen.  
**You:** Tell me about *(event name)*  
**You:** Yes, book this  
**You:** Rahul Sharma and Priya Verma  
*(then email, phone, gender, date of birth for each guest)*  
Then click **Book / Pay** on the page.

### Flight (step by step — hear WHERE WHEN WHY WHO)

**You:** Hey Sabraah  
**You:** Book a flight  
**Sabraah:** Where would you like to go?  
**You:** Indore to Delhi  
**Sabraah:** When do you want to travel?  
**You:** Tomorrow  
**Sabraah:** What's the trip for — leisure, family, business, honeymoon, medical, emergency, or something else?  
**You:** Leisure  
**Sabraah:** Who is travelling?  
**You:** One passenger  
**Sabraah:** Which cabin — economy, premium economy, or business class?  
**You:** Economy  

### Flight (natural)

**You:** Hey Sabraah  
**You:** I want to book a flight from Indore to Delhi tomorrow, one passenger, other.  
**Sabraah:** Asks cabin if you have not said it — `economy`, `premium economy`, or `business class`.  
**You:** Economy.  
**Sabraah:** Flights on screen, with class shown, plus one recommended option. She asks if you want them read slowly or will pick from the screen.  
**You:** Read them  
**Sabraah:** Speaks Option 1, pauses, Option 2, … then recommends one.  
**You:** Option 1  

### Train (step by step)

**You:** Book a train  
**You:** Pune to Delhi  
**You:** Tomorrow  
**You:** Personal work  
**You:** Two passengers  
**You:** Option 1  
**You:** Veg  
**You:** Rahul, Priya  
**You:** 9876543210  
**You:** Yes  

### Family trip in one breath

**You:** Take my family from Delhi to Goa on 20 September, two adults two kids, relaxed, not a crazy itinerary, fly please.

### Event venue

**You:** Hey Sabraah  
**You:** Wedding venue in Jaipur for 200 guests, banquet, food and decoration, budget 5 lakh.  
**Sabraah:** Venue names on screen, opens the venue list.  
**You:** Option 1  
RFP page opens with guests, date, budget, and food/decoration notes filled. Submit there.

---

## Tips

- English only for now.
- `two` and `2 passengers` both work. `just me` = 1.
- `via flight` means **how**, not a city. Say `Indore to Delhi via flight`.
- Do not re-answer things already in the chat — she should remember this session.
- Refresh the page if an old chat still talks about **trains** after you asked for a **flight**.
- Logged-in Super Travel JWT is needed for paid checkout; search can stay anonymous.
