# Sabraah — How to use

Speak or type in **English**. Sabraah answers in English.

You do **not** need option cards for goals. Say the trip in a normal sentence. Sabraah fills WHO / WHERE / WHEN / WHY / HOW from what you already said, and only asks what is still missing.

---

## Where to open it

| App | URL | Use for |
|-----|-----|---------|
| Super Travel site widget (orb) | `http://localhost:3000/home` | Main product: talk on the website |
| Sabraah AI voice UI | `http://127.0.0.1:8000` | Standalone voice page |
| Admin / agents | `http://127.0.0.1:8000/admin` | Prompt and first-message edits |

Always-listen: say **Hey Sabraah** (or **Hey Sabrah**). Or type in the box and press Send. Or press **Talk**.

Start the stack in this order: **api-repository :8002** → **sabrah-ai :8000** → **super-travel-web :3000**.

---

## What Sabraah is deciding

Every conversation feeds this model. You can answer several in one sentence.

| Question | What it means | Example you can say |
|----------|----------------|---------------------|
| **WHO?** | Who is travelling | `just me` / `with my wife` / `two adults and one child` / `family` |
| **WHERE?** | From → to | `Indore to Delhi` / `Pune to Mumbai` |
| **WHEN?** | Travel date | `tomorrow` / `20 September 2026` / `flexible dates` |
| **WHY?** | Purpose (better recommendations) | `leisure` / `family` / `business` / `wedding` / `honeymoon` / `other` |
| **HOW?** | Flight, train, hotel, or event | `book a flight` / `train` / `hotel in Goa` / `book an event` |
| **HOW MUCH?** | Budget if you have one | `budget under 50000` / `2 lakh` |
| **WHAT THEY VALUE** | Price vs time vs comfort | `cheapest` / `fastest` / `cheap but not uncomfortable` / `direct flight` |
| **WHAT THEY NEED** | Restrictions | `vegetarian` / `no 4 AM flights` / `wheelchair` / `relaxed itinerary` |

Do **not** wait for a form. Example:

> I want to take my family from Indore to Delhi tomorrow, two adults one kid, cheapest direct flight.

Sabraah should search flights, not trains, and not ask those facts again.

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
2. Or type `Hi Sabrah` and Send

Sabraah invites the trip. Then either dump the trip in one line, or pick a module below.

---

## 2. Flights

**For:** plane tickets on Super Travel (search → pick → fare/extras → checkout page). Card is **not** charged in voice.

**How — shortest path:**

| Step | You say | Why |
|------|---------|-----|
| 1 | `I want to book a flight` | Sets HOW = flight |
| 2 | `Indore to Delhi` | WHERE (do not say “via flight” as a city; `via flight` is OK as transport) |
| 3 | `Tomorrow` or `20 September 2026` | WHEN |
| 4 | `One passenger` / `two adults and one child` | WHO |
| 5 | `Leisure` / `business` / `wedding` / `other` | WHY (skipped if you already said family/business) |
| 6 | Screen shows flights → `Option 1` | Pick the flight |
| 7 | `Saver` / `Flexi` / `Super`, or `skip` | Fare type — only if that airline returned more than one |
| 8 | `Veg meal` / `no meal` | Paid meals — only if that fare has them |
| 9 | `5 kg extra bag` / `skip` | Extra baggage — only if offered on that fare |
| 10 | `Priority check-in` / `skip` | Only if that fare has priority check-in |

Then checkout opens so you can finish passenger details and payment. Sabraah asks **only** extras that Super Travel actually returned for that fare (same list as Fare Details / Extra Services). If a fare has no meals or bags, she will not invent them.

**Natural one-liners (preferred):**

- `Book a flight from Indore to Delhi tomorrow for 1 passenger`
- `Delhi to Dubai, round trip, 12 October, 2 adults, cheapest`
- `Direct morning flight Pune to Bangalore, business class`
- `Cheap but not uncomfortable, and I don't want to wake up at 4 AM`

**Preferences you can add anytime:**

| You say | Sabraah understands |
|---------|---------------------|
| `cheapest` / `budget` | Value = price |
| `fastest` / `shortest` | Value = time |
| `direct` / `non-stop` | Direct only |
| `economy` / `premium economy` / `business class` | Cabin |
| `round trip` / `one way` / `multi city` | Trip type |

After search she recommends **one** option (cheapest vs fastest vs fewer stops) and keeps the list on screen.

---

## 3. Trains

**For:** train search, pick a train, meal, passenger names, phone, confirm booking.

**How:**

| Step | You say | Why |
|------|---------|-----|
| 1 | `Book a train` | HOW = train |
| 2 | `Pune to Delhi` | WHERE |
| 3 | Date | WHEN |
| 4 | `Two passengers` | WHO |
| 5 | Purpose (`personal work` / `wedding` / `other`) | WHY |
| 6 | `Option 1` or train name | Pick from screen |
| 7 | `Veg` / `Non veg` / `Jain` / `No meal` | Meal once |
| 8 | `Rahul, Priya` | Names |
| 9 | `9876543210` | Phone |
| 10 | `Yes` | Confirm |

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

**For:** concerts / shows / events, then open the booking page with guest details. Voice does **not** take payment.

**How:**

| Step | You say | Why |
|------|---------|-----|
| 1 | `Book an event` / `what events are on` | Search |
| 2 | Listen to Option 1, 2, … | She speaks names |
| 3 | `Tell me about option 2` or the event name | Details |
| 4 | `Yes, book this` | Confirm intent |
| 5 | Guest **full name, email, 10-digit phone, gender, date of birth** | Required before the page opens |
| 6 | Booking page opens — you click Book / Pay | Checkout |

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

### Flight (natural)

**You:** Hey Sabraah  
**You:** I want to book a flight from Indore to Delhi tomorrow, one passenger, other.  
**Sabraah:** Flights on screen + one recommended option.  
**You:** Option 1  

### Train (step by step)

**You:** Book a train  
**You:** Pune to Delhi  
**You:** Tomorrow  
**You:** Two passengers  
**You:** Personal work  
**You:** Option 1  
**You:** Veg  
**You:** Rahul, Priya  
**You:** 9876543210  
**You:** Yes  

### Family trip in one breath

**You:** Take my family from Delhi to Goa on 20 September, two adults two kids, relaxed, not a crazy itinerary, fly please.

---

## Tips

- English only for now.
- `two` and `2 passengers` both work. `just me` = 1.
- `via flight` means **how**, not a city. Say `Indore to Delhi via flight`.
- Do not re-answer things already in the chat — she should remember this session.
- Refresh the page if an old chat still talks about **trains** after you asked for a **flight**.
- Logged-in Super Travel JWT is needed for paid checkout; search can stay anonymous.
