(() => {
  const statusLabel = document.getElementById("statusLabel");
  const connectionDot = document.getElementById("connectionDot");
  const orb = document.getElementById("orb");
  const transcript = document.getElementById("transcript");
  const errorBanner = document.getElementById("errorBanner");
  const talkBtn = document.getElementById("talkBtn");
  const stopBtn = document.getElementById("stopBtn");
  const muteBtn = document.getElementById("muteBtn");
  const resetBtn = document.getElementById("resetBtn");
  const textInput = document.getElementById("textInput");
  const sendTextBtn = document.getElementById("sendTextBtn");
  const wakeToggle = document.getElementById("wakeToggle");
  const wakeHint = document.getElementById("wakeHint");
  const offeringsEl = document.getElementById("offerings");
  const travelerFile = document.getElementById("travelerFile");
  const uploadTravelersBtn = document.getElementById("uploadTravelersBtn");
  const uploadStatus = document.getElementById("uploadStatus");

  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition || null;

  const WAKE_PATTERNS = [
    /\b(?:hey|hi|hello|ok(?:ay)?)\s+sabr+a+h?\b/i,
    /\bhe+\s+sabr+a+h?\b/i,
  ];

  // Faster end-of-speech detection (was staying on Listening too long).
  const MAX_RECORD_MS = 9000;
  const SILENCE_MS = 750;
  const SPEECH_LEVEL = 0.01;
  const NO_SPEECH_MS = 4000;
  const MIN_SPEECH_MS = 280;

  let sessionId = null;
  let mediaRecorder = null;
  let mediaStream = null;
  let chunks = [];
  let muted = false;
  let busy = false;
  let currentAudio = null;
  let wakeRecognition = null;
  let wakeEnabled = false;
  let wakeStarting = false;
  let commandMode = false;
  let silenceTimer = null;
  let maxRecordTimer = null;
  let noSpeechTimer = null;
  let analyserCtx = null;
  let analyserRaf = null;
  let heardSpeech = false;
  let speechStartedAt = 0;
  let autoListenAfterReply = false;
  let pendingFollowUpListen = false;

  function setStatus(label, mode = "idle") {
    statusLabel.textContent = label;
    orb.className = `orb ${mode}`;
  }

  function idleLabel() {
    return wakeEnabled ? "Say Hey Sabrah" : "Ready";
  }

  function idleMode() {
    return wakeEnabled ? "waiting" : "idle";
  }

  function restoreIdle() {
    if (!busy && !commandMode) {
      setStatus(idleLabel(), idleMode());
    }
  }

  function setConnection(ok, warn = false) {
    connectionDot.className = `dot${ok ? "" : warn ? " warn" : " error"}`;
  }

  function showError(message) {
    errorBanner.hidden = !message;
    errorBanner.textContent = message || "";
  }

  function addBubble(role, text) {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${role}`;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = role === "user" ? "You" : "Sabrah";
    const body = document.createElement("div");
    body.textContent = text;
    bubble.appendChild(who);
    bubble.appendChild(body);
    transcript.appendChild(bubble);
    transcript.scrollTop = transcript.scrollHeight;
  }

  function clearOfferings() {
    offeringsEl.hidden = true;
    offeringsEl.innerHTML = "";
  }

  function money(value, currency = "INR") {
    if (value == null || value === "") return "";
    const num = Number(value);
    if (Number.isNaN(num)) return String(value);
    return `${currency} ${Math.round(num)}`;
  }

  function renderOfferings(offerings) {
    clearOfferings();
    if (!offerings || typeof offerings !== "object") return;

    const sections = [];
    const pushItems = (key, title, rows, formatter) => {
      if (!Array.isArray(rows) || !rows.length) return;
      sections.push({ key, title, rows, formatter });
    };

    pushItems("goals", "What do you want to do?", offerings.goals, (row, index) => ({
      label: `Option ${index + 1}`,
      title: row.name || row.goal || "Action",
      meta: row.hint || null,
      chooseText: row.name || `Option ${index + 1}`,
    }));

    pushItems("purposes", "Why are you traveling?", offerings.purposes, (row, index) => ({
      label: `Option ${index + 1}`,
      title: row.name || row.purpose || "Purpose",
      meta: null,
      chooseText: row.name || `Option ${index + 1}`,
    }));

    pushItems("trains", "Trains", offerings.trains, (row, index) => ({
      label: row.recommended
        ? `Best · Option ${index + 1}`
        : row.leg === "return"
          ? `Return ${index + 1}`
          : `Option ${index + 1}`,
      title: row.name || row.id || "Train",
      meta: [
        row.availability_status
          ? String(row.availability_status)
          : null,
        row.departure_time && row.arrival_time
          ? `${row.departure_time} → ${row.arrival_time}`
          : null,
        row.duration,
        row.class || row.travel_class,
        row.source_station && row.platform
          ? `${row.source_station} · Pf ${row.platform}`
          : null,
        row.unit_price != null
          ? `${money(row.unit_price, row.currency || "INR")} / person`
          : null,
        row.price != null
          ? `Total ${money(row.price, row.currency || "INR")}`
          : money(row.price, row.currency || "INR"),
        (() => {
          const status = String(row.availability_status || "").toUpperCase();
          if (status === "AVAILABLE") {
            return row.available_seats != null
              ? `CNF ${row.available_seats} available`
              : "AVAILABLE";
          }
          if (status === "RAC") {
            return row.rac_seats != null
              ? `RAC ${row.rac_seats} (no CNF)`
              : "RAC only";
          }
          if (status === "WL") {
            return row.waiting_list != null
              ? `WL ${row.waiting_list} (no CNF/RAC)`
              : "Waiting list";
          }
          if (status === "NOT_AVAILABLE") return "No seats";
          return row.available_seats != null
            ? `CNF ${row.available_seats}`
            : null;
        })(),
        row.availability_message || null,
        row.wishlisted ? "Wishlisted" : null,
        row.recommendation_reason || null,
      ]
        .filter(Boolean)
        .join(" · "),
      chooseText: `Option ${index + 1}, ${row.name || row.id}`,
    }));

    pushItems("live_status", "Live train status", offerings.live_status, (row, index) => ({
      label: row.live_status || `Live ${index + 1}`,
      title: row.train_name || row.train_id || "Train",
      meta: [
        row.source && row.destination ? `${row.source} → ${row.destination}` : null,
        row.status_message || null,
        row.availability_status || null,
        row.delay_minutes != null ? `Delay ${row.delay_minutes}m` : null,
        row.eta_destination ? `ETA ${row.eta_destination}` : null,
      ]
        .filter(Boolean)
        .join(" · "),
      chooseText: `Live status ${row.train_name || row.train_id || ""}`.trim(),
    }));

    pushItems("flights", "Flights", offerings.flights, (row, index) => ({
      label: `Flight ${index + 1}`,
      title: row.airline || row.name || row.id || "Flight",
      meta: [
        row.departure_time && row.arrival_time
          ? `${row.departure_time} → ${row.arrival_time}`
          : null,
        row.duration,
        money(row.price, row.currency || "INR"),
      ]
        .filter(Boolean)
        .join(" · "),
      chooseText: `Flight option ${index + 1}, ${row.airline || row.id}`,
    }));

    pushItems("buses", "Buses", offerings.buses, (row, index) => {
      const legs = Array.isArray(row.legs) ? row.legs : [];
      const transferNote =
        legs.length > 1
          ? `Connecting: ${legs
              .map((leg) => `${leg.from}→${leg.to}`)
              .join(" then ")}`
          : "Direct";
      return {
        label: `Bus ${index + 1}`,
        title: row.name || row.id || "Bus",
        meta: [
          row.operator,
          row.departure_time && row.arrival_time
            ? `${row.departure_time} → ${row.arrival_time}`
            : null,
          row.duration,
          row.bus_type,
          transferNote,
          money(row.price, row.currency || "INR"),
        ]
          .filter(Boolean)
          .join(" · "),
        chooseText: `Bus option ${index + 1}, ${row.name || row.id}`,
      };
    });

    pushItems("hotels", "Hotels", offerings.hotels, (row, index) => ({
      label: `Hotel ${index + 1}`,
      title: row.name || row.id || "Hotel",
      meta: [
        row.city,
        row.rating != null ? `${row.rating}★` : null,
        money(row.total_price || row.price_per_night, row.currency || "INR"),
      ]
        .filter(Boolean)
        .join(" · "),
      chooseText: `Hotel option ${index + 1}, ${row.name || row.id}`,
    }));

    pushItems("packages", "Packages", offerings.packages, (row, index) => ({
      label: `Package ${index + 1}`,
      title: row.name || row.id || "Package",
      meta: [
        row.nights != null ? `${row.nights} nights` : null,
        Array.isArray(row.includes) ? row.includes.join(", ") : null,
        money(row.price, row.currency || "INR"),
      ]
        .filter(Boolean)
        .join(" · "),
      chooseText: `Package option ${index + 1}, ${row.name || row.id}`,
    }));

    pushItems("local", "Local transport", offerings.local, (row, index) => ({
      label: `Local ${index + 1}`,
      title: row.name || row.id || "Local ride",
      meta: [
        row.type,
        row.pickup && row.dropoff ? `${row.pickup} → ${row.dropoff}` : row.city,
        row.duration,
        money(row.price, row.currency || "INR"),
      ]
        .filter(Boolean)
        .join(" · "),
      chooseText: `Local option ${index + 1}, ${row.name || row.id}`,
    }));

    if (Array.isArray(offerings.wishlist) && offerings.wishlist.length) {
      sections.push({
        key: "wishlist",
        title: "Wishlist",
        rows: offerings.wishlist,
        formatter: (row, index) => ({
          label: `Saved ${index + 1}`,
          title: row.name || row.id || "Train",
          meta: [row.source, row.destination].filter(Boolean).join(" → "),
          chooseText: `Wishlist train ${row.name || row.id}`,
        }),
      });
    }

    if (offerings.train_details && typeof offerings.train_details === "object") {
      const d = offerings.train_details;
      sections.push({
        key: "train_details",
        title: "Stations & platforms",
        rows: [d],
        formatter: (row) => ({
          label: "Details",
          title: row.name || row.id || "Train",
          meta: [
            row.source_station && row.platform
              ? `${row.source_station} · Platform ${row.platform}`
              : null,
            row.destination_station || null,
            row.connections_note || null,
          ]
            .filter(Boolean)
            .join(" · "),
          details: Array.isArray(row.stops)
            ? row.stops.map(
                (s) =>
                  `${s.station || ""} (${s.code || ""}) pf ${s.platform || "—"}`
              )
            : [],
          chooseText: `Tell me more about train ${row.id || ""}`.trim(),
        }),
      });
    }

    if (offerings.weather && typeof offerings.weather === "object") {
      const w = offerings.weather;
      sections.push({
        key: "weather",
        title: "Travel alert",
        rows: [w],
        formatter: (row) => ({
          label: row.alert ? "Alert" : "Weather",
          title: row.summary || row.city || "Weather",
          meta: row.tip || "",
          chooseText: "Thanks for the weather tip",
        }),
      });
    }

    if (offerings.charter && typeof offerings.charter === "object") {
      const c = offerings.charter;
      sections.push({
        key: "charter",
        title: "Charter request",
        rows: [c],
        formatter: (row) => ({
          label: row.status || "Request",
          title: row.request_id || "Charter",
          meta: row.message || "",
          chooseText: "Got it, waiting for SabRaah sales",
        }),
      });
    }

    if (offerings.support && typeof offerings.support === "object") {
      const s = offerings.support;
      sections.push({
        key: "support",
        title: "Support",
        rows: [s],
        formatter: (row) => ({
          label: row.status || "Ticket",
          title: row.ticket_id || row.refund_id || "Support",
          meta: row.message || "",
          chooseText: "Understood",
        }),
      });
    }

    if (offerings.feedback && typeof offerings.feedback === "object") {
      const f = offerings.feedback;
      sections.push({
        key: "feedback",
        title: "Feedback",
        rows: [f],
        formatter: (row) => ({
          label: "Thanks",
          title: row.feedback_id || "Feedback saved",
          meta: row.message || "",
          chooseText: "Thank you",
        }),
      });
    }

    if (offerings.plan && typeof offerings.plan === "object") {
      const plan = offerings.plan;
      sections.push({
        key: "plan",
        title: "Package contents",
        rows: [plan],
        formatter: (row) => {
          const includes = Array.isArray(row.includes) ? row.includes : [];
          const lines = includes.length
            ? includes.map((part) => {
                const label = part.label || part.type || "Item";
                const name = part.name || "";
                const detail = part.detail ? ` — ${part.detail}` : "";
                return `${label}: ${name}${detail}`;
              })
            : Array.isArray(row.day_plan)
              ? row.day_plan
              : [];
          const dates =
            row.departure_date && row.return_date
              ? `${row.departure_date} → ${row.return_date}`
              : row.departure_date || "";
          const total = money(
            row.estimated_total || row.price,
            row.currency || "INR"
          );
          return {
            label: "Your package",
            title: row.summary || row.id || "Package plan",
            meta: [dates, total].filter(Boolean).join(" · "),
            details: lines,
            chooseText: `Yes, book this package ${row.id || ""}`.trim(),
          };
        },
      });
    }

    if (offerings.booking && typeof offerings.booking === "object") {
      const b = offerings.booking;
      sections.push({
        key: "booking",
        title: "Booking confirmed",
        rows: [b],
        formatter: (row) => {
          const seats = Array.isArray(row.seats) ? row.seats : [];
          const names = Array.isArray(row.traveler_names)
            ? row.traveler_names
            : [];
          const details = [
            row.train_name
              ? `Train: ${row.train_name}${row.train_id ? ` (${row.train_id})` : ""}`
              : row.train_id
                ? `Train ID: ${row.train_id}`
                : null,
            row.source && row.destination
              ? `Route: ${row.source} → ${row.destination}`
              : null,
            row.departure_date ? `Date: ${row.departure_date}` : null,
            row.departure_time || row.arrival_time
              ? `Schedule: ${row.departure_time || "—"} → ${row.arrival_time || "—"}${
                  row.duration ? ` (${row.duration})` : ""
                }`
              : null,
            row.travel_class ? `Class: ${row.travel_class}` : null,
            row.platform ? `Platform: ${row.platform}` : null,
            row.source_station
              ? `From: ${row.source_station}`
              : null,
            row.destination_station
              ? `To: ${row.destination_station}`
              : null,
            names.length ? `Passengers: ${names.join(", ")}` : null,
            row.meal_preference ? `Meal: ${row.meal_preference}` : null,
            row.allergies ? `Allergies: ${row.allergies}` : null,
            row.total_price != null
              ? `Amount: INR ${Number(row.total_price).toFixed(0)}`
              : null,
            ...seats.map(
              (s) => `${s.passenger}: ${s.seat_label || s.berth}`
            ),
          ].filter(Boolean);
          return {
            label: row.booking_id || "Booking",
            title: row.train_name
              ? `${row.train_name} · Platform ${row.platform || "—"}`
              : `Platform ${row.platform || "—"}`,
            meta: [row.source_station, row.destination_station]
              .filter(Boolean)
              .join(" · "),
            details,
            chooseText: "Thanks",
          };
        },
      });
    }

    if (Array.isArray(offerings.travelers) && offerings.travelers.length) {
      pushItems("travelers", "Uploaded travelers", offerings.travelers, (row, index) => ({
        label: `Traveler ${index + 1}`,
        title: row.name || "Name",
        meta: [row.phone, row.meal, row.allergies].filter(Boolean).join(" · "),
        chooseText: `Traveler ${row.name}`,
      }));
    }

    if (!sections.length) return;

    const onlyKey = sections.length === 1 ? sections[0].key : null;
    const head = document.createElement("p");
    head.className = "offerings-head";
    if (onlyKey === "flights") {
      head.textContent = "Step 1/4 — Flight (or skip flight)";
    } else if (onlyKey === "goals") {
      head.textContent = "Choose one — book, cancel, or refund";
    } else if (onlyKey === "purposes") {
      head.textContent = "Why are you traveling?";
    } else if (onlyKey === "trains") {
      head.textContent = "Choose a train — price per person + total";
    } else if (onlyKey === "local") {
      head.textContent = "Step 3/4 — Local transport (or skip)";
    } else if (onlyKey === "buses") {
      head.textContent = "Step 2/3 — Bus (or skip bus)";
    } else if (onlyKey === "hotels") {
      head.textContent = "Step 4/4 — Hotel";
    } else if (onlyKey === "plan") {
      head.textContent = "Your journey / package";
      } else if (onlyKey === "booking") {
      head.textContent = "Full booking — train, seats & passengers";
    } else {
      head.textContent = "Options on screen — tap or say an option";
    }
    offeringsEl.appendChild(head);

    sections.forEach((section) => {
      const group = document.createElement("div");
      group.className = "offer-group";
      const h3 = document.createElement("h3");
      h3.textContent = section.title;
      group.appendChild(h3);
      const list = document.createElement("div");
      list.className = "offer-list";
      section.rows.forEach((row, index) => {
        const view = section.formatter(row, index);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "offer-item";
        const detailsHtml = Array.isArray(view.details) && view.details.length
          ? `<ul class="opt-details">${view.details
              .map((line) => `<li>${line}</li>`)
              .join("")}</ul>`
          : "";
        btn.innerHTML = `
          <span class="opt-label">${view.label}</span>
          <span class="opt-title">${view.title}</span>
          <span class="opt-meta">${view.meta || ""}</span>
          ${detailsHtml}
        `;
        btn.addEventListener("click", () => {
          autoListenAfterReply = false;
          sendText(view.chooseText);
        });
        list.appendChild(btn);
      });
      group.appendChild(list);
      offeringsEl.appendChild(group);
    });

    offeringsEl.hidden = false;
    offeringsEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function stopPlayback() {
    if (currentAudio) {
      currentAudio.pause();
      currentAudio.src = "";
      currentAudio = null;
    }
  }

  function playWakeChime() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const now = ctx.currentTime;
      const tones = [523.25, 659.25];
      tones.forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.08, now + 0.02 + i * 0.05);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.28 + i * 0.08);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now + i * 0.05);
        osc.stop(now + 0.35 + i * 0.08);
      });
      setTimeout(() => ctx.close().catch(() => {}), 600);
    } catch {
      /* optional */
    }
  }

  function normalizeHeard(text) {
    return String(text || "")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function isWakePhrase(text) {
    const cleaned = normalizeHeard(text);
    if (!cleaned) return false;
    return WAKE_PATTERNS.some((re) => re.test(cleaned));
  }

  function stopSilenceWatch() {
    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
    if (maxRecordTimer) {
      clearTimeout(maxRecordTimer);
      maxRecordTimer = null;
    }
    if (noSpeechTimer) {
      clearTimeout(noSpeechTimer);
      noSpeechTimer = null;
    }
    if (analyserRaf) {
      cancelAnimationFrame(analyserRaf);
      analyserRaf = null;
    }
    if (analyserCtx) {
      analyserCtx.close().catch(() => {});
      analyserCtx = null;
    }
    heardSpeech = false;
    speechStartedAt = 0;
  }

  function startSilenceWatch(stream) {
    stopSilenceWatch();
    heardSpeech = false;
    speechStartedAt = 0;

    maxRecordTimer = setTimeout(() => {
      if (commandMode) stopListening();
    }, MAX_RECORD_MS);

    noSpeechTimer = setTimeout(() => {
      if (commandMode && !heardSpeech) stopListening({ discard: true });
    }, NO_SPEECH_MS);

    try {
      analyserCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = analyserCtx.createMediaStreamSource(stream);
      const analyser = analyserCtx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      const data = new Uint8Array(analyser.fftSize);

      const tick = () => {
        if (!commandMode) return;
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        if (rms > SPEECH_LEVEL) {
          if (!heardSpeech) {
            heardSpeech = true;
            speechStartedAt = Date.now();
            if (noSpeechTimer) {
              clearTimeout(noSpeechTimer);
              noSpeechTimer = null;
            }
          }
          if (silenceTimer) {
            clearTimeout(silenceTimer);
            silenceTimer = null;
          }
        } else if (heardSpeech && !silenceTimer) {
          const spokenFor = Date.now() - speechStartedAt;
          if (spokenFor >= MIN_SPEECH_MS) {
            silenceTimer = setTimeout(() => {
              if (commandMode) stopListening();
            }, SILENCE_MS);
          }
        }
        analyserRaf = requestAnimationFrame(tick);
      };
      analyserRaf = requestAnimationFrame(tick);
    } catch {
      /* max duration fallback */
    }
  }

  function stopWakeListening({ keepEnabled = true } = {}) {
    if (wakeRecognition) {
      try {
        wakeRecognition.onend = null;
        wakeRecognition.onerror = null;
        wakeRecognition.onresult = null;
        wakeRecognition.stop();
      } catch {
        /* already stopped */
      }
      wakeRecognition = null;
    }
    if (!keepEnabled) wakeEnabled = false;
  }

  function startWakeListening() {
    if (!wakeEnabled || busy || commandMode || wakeStarting || !SpeechRecognition) {
      return;
    }
    if (wakeRecognition) return;

    wakeStarting = true;
    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    recognition.maxAlternatives = 3;

    recognition.onresult = (event) => {
      if (!wakeEnabled || busy || commandMode) return;
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = Array.from(result)
          .map((alt) => alt.transcript)
          .join(" ");
        if (isWakePhrase(text)) {
          onWakeDetected();
          return;
        }
      }
    };

    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        wakeToggle.checked = false;
        wakeEnabled = false;
        wakeHint.hidden = true;
        stopWakeListening({ keepEnabled: false });
        showError(
          "Microphone permission needed for Hey Sabrah. Allow mic access, then turn Always listen back on."
        );
        restoreIdle();
      }
    };

    recognition.onend = () => {
      wakeRecognition = null;
      wakeStarting = false;
      if (wakeEnabled && !busy && !commandMode) {
        setTimeout(() => startWakeListening(), 200);
      }
    };

    try {
      recognition.start();
      wakeRecognition = recognition;
      restoreIdle();
    } catch {
      wakeRecognition = null;
      setTimeout(() => startWakeListening(), 400);
    } finally {
      wakeStarting = false;
    }
  }

  async function onWakeDetected() {
    if (busy || commandMode) return;
    stopWakeListening({ keepEnabled: true });
    showError("");
    playWakeChime();
    // Greeting turn (no long empty recording).
    autoListenAfterReply = true;
    await sendText("Hey Sabrah");
  }

  async function playAudioBase64(b64, mime = "audio/mpeg") {
    if (muted || !b64) {
      restoreIdle();
      return;
    }
    stopPlayback();
    return new Promise((resolve) => {
      const audio = new Audio(`data:${mime};base64,${b64}`);
      currentAudio = audio;
      setStatus("Speaking", "speaking");
      audio.onended = () => {
        if (currentAudio === audio) currentAudio = null;
        restoreIdle();
        resolve();
      };
      audio.onerror = () => {
        showError("Could not play Sabrah's voice response.");
        restoreIdle();
        resolve();
      };
      audio.play().catch(() => {
        showError("Browser blocked audio playback. Click anywhere and try again.");
        restoreIdle();
        resolve();
      });
    });
  }

  async function ensureSession() {
    if (sessionId) return sessionId;
    const res = await fetch("/api/v1/sessions", { method: "POST" });
    if (!res.ok) throw new Error("Could not start a conversation session.");
    const data = await res.json();
    sessionId = data.session_id;
    return sessionId;
  }

  async function checkHealth() {
    try {
      const res = await fetch("/health");
      const data = await res.json();
      if (data.travel_backend === "reachable") {
        setConnection(true);
      } else {
        setConnection(false, true);
        showError(
          "Travel backend is unreachable. Start sabrah-travel-backend on port 8001."
        );
      }
    } catch {
      setConnection(false);
      showError("Cannot reach Sabrah AI server.");
    }
  }

  function extractError(payload) {
    if (!payload) return "Something went wrong.";
    if (typeof payload.detail === "string") return payload.detail;
    if (payload.detail && payload.detail.message) return payload.detail.message;
    if (payload.message) return payload.message;
    return "Something went wrong.";
  }

  function extractErrorCode(payload) {
    if (!payload) return "";
    if (payload.detail && typeof payload.detail === "object") {
      return String(payload.detail.code || "");
    }
    return String(payload.code || "");
  }

  function isSessionGone(payload, message) {
    const code = extractErrorCode(payload);
    const text = String(message || "").toLowerCase();
    return (
      code === "session_not_found" ||
      text.includes("session expired") ||
      text.includes("session not found")
    );
  }

  async function refreshSession() {
    sessionId = null;
    return ensureSession();
  }

  async function postChatText(message) {
    await ensureSession();
    return fetch("/api/v1/chat/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
  }

  async function postChatVoice(blob) {
    await ensureSession();
    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("audio", blob, "speech.webm");
    return fetch("/api/v1/chat/voice", {
      method: "POST",
      body: form,
    });
  }

  function maybeRedirectPayment(payload) {
    if (!payload || !payload.payment_url) return false;
    const url = payload.payment_url.startsWith("http")
      ? payload.payment_url
      : payload.payment_url;
    window.setTimeout(() => {
      window.location.href = url;
    }, 400);
    return true;
  }

  async function handleChatResponse(res) {
    const payload = await res.json().catch(() => null);
    if (!res.ok) {
      const err = new Error(extractError(payload));
      err.payload = payload;
      err.status = res.status;
      throw err;
    }
    addBubble("user", payload.user_text);
    addBubble("assistant", payload.assistant_text);
    if (payload.offerings && Object.keys(payload.offerings).length) {
      renderOfferings(payload.offerings);
    }
    if (payload.tts_error) {
      showError(payload.tts_error);
    }
    await playAudioBase64(payload.audio_base64, payload.audio_mime_type || "audio/mpeg");

    if (maybeRedirectPayment(payload)) {
      autoListenAfterReply = false;
      return;
    }

    if (autoListenAfterReply) {
      autoListenAfterReply = false;
      pendingFollowUpListen = true;
      setTimeout(() => {
        pendingFollowUpListen = false;
        if (!busy && !commandMode) startListening({ followUp: true });
      }, 350);
    }
  }

  async function sendText(message) {
    if (!message || busy) return;
    busy = true;
    stopWakeListening({ keepEnabled: true });
    showError("");
    stopPlayback();
    setStatus("Thinking", "thinking");
    talkBtn.disabled = true;
    sendTextBtn.disabled = true;
    try {
      let res = await postChatText(message);
      if (!res.ok) {
        const payload = await res.clone().json().catch(() => null);
        if (isSessionGone(payload, extractError(payload))) {
          await refreshSession();
          res = await postChatText(message);
        }
      }
      await handleChatResponse(res);
      showError("");
    } catch (err) {
      if (isSessionGone(err.payload, err.message)) {
        try {
          await refreshSession();
          const res = await postChatText(message);
          await handleChatResponse(res);
          showError("");
        } catch (retryErr) {
          showError(retryErr.message || "Request failed.");
          restoreIdle();
          autoListenAfterReply = false;
        }
      } else {
        showError(err.message || "Request failed.");
        restoreIdle();
        autoListenAfterReply = false;
      }
    } finally {
      busy = false;
      talkBtn.disabled = false;
      sendTextBtn.disabled = false;
      if (wakeEnabled && !commandMode && !autoListenAfterReply && !pendingFollowUpListen) {
        startWakeListening();
      }
    }
  }

  async function sendVoice(blob) {
    busy = true;
    showError("");
    setStatus("Thinking", "thinking");
    talkBtn.disabled = true;
    stopBtn.disabled = true;
    try {
      let res = await postChatVoice(blob);
      if (!res.ok) {
        const payload = await res.clone().json().catch(() => null);
        if (isSessionGone(payload, extractError(payload))) {
          await refreshSession();
          res = await postChatVoice(blob);
        }
      }
      // After each answered question, keep conversation moving.
      autoListenAfterReply = wakeEnabled;
      await handleChatResponse(res);
      showError("");
    } catch (err) {
      if (isSessionGone(err.payload, err.message)) {
        try {
          await refreshSession();
          autoListenAfterReply = wakeEnabled;
          const res = await postChatVoice(blob);
          await handleChatResponse(res);
          showError("");
        } catch (retryErr) {
          showError(retryErr.message || "Voice request failed.");
          restoreIdle();
          autoListenAfterReply = false;
        }
      } else {
        showError(err.message || "Voice request failed.");
        restoreIdle();
        autoListenAfterReply = false;
      }
    } finally {
      busy = false;
      talkBtn.disabled = false;
      stopBtn.disabled = true;
      talkBtn.textContent = "Start Talking";
      if (wakeEnabled && !commandMode && !autoListenAfterReply && !pendingFollowUpListen) {
        startWakeListening();
      }
    }
  }

  async function startListening({ followUp = false } = {}) {
    if (busy || commandMode) return;
    showError("");
    stopPlayback();
    stopWakeListening({ keepEnabled: true });

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const denied =
        err && (err.name === "NotAllowedError" || err.name === "PermissionDeniedError");
      showError(
        denied
          ? "Microphone permission denied. Allow microphone access in your browser settings and try again."
          : "Could not access the microphone on this device."
      );
      setConnection(false);
      if (wakeEnabled) startWakeListening();
      else restoreIdle();
      return;
    }

    chunks = [];
    commandMode = true;
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    mediaRecorder = new MediaRecorder(mediaStream, { mimeType: mime });
    mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) chunks.push(event.data);
    };
    mediaRecorder.onstop = async () => {
      const discarded = mediaRecorder && mediaRecorder._discard;
      stopSilenceWatch();
      commandMode = false;
      if (mediaStream) {
        mediaStream.getTracks().forEach((t) => t.stop());
        mediaStream = null;
      }
      const blob = new Blob(chunks, { type: mime });
      chunks = [];
      talkBtn.disabled = false;
      stopBtn.disabled = true;
      talkBtn.textContent = "Start Talking";

      if (discarded || !blob.size || blob.size < 800) {
        if (!followUp) {
          showError("No speech detected. Please try again.");
        }
        restoreIdle();
        if (wakeEnabled) startWakeListening();
        return;
      }
      setStatus("Thinking", "thinking");
      await sendVoice(blob);
    };

    mediaRecorder.start(200);
    startSilenceWatch(mediaStream);
    setStatus("Listening", "listening");
    talkBtn.disabled = true;
    stopBtn.disabled = false;
    talkBtn.textContent = "Listening…";
  }

  function stopListening({ discard = false } = {}) {
    stopSilenceWatch();
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder._discard = discard;
      // Flip UI immediately so Listening does not linger.
      setStatus(discard ? idleLabel() : "Thinking", discard ? idleMode() : "thinking");
      mediaRecorder.stop();
    }
    stopBtn.disabled = true;
  }

  function enableWakeMode(enabled) {
    wakeEnabled = Boolean(enabled);
    wakeHint.hidden = !wakeEnabled;
    if (!wakeEnabled) {
      stopWakeListening({ keepEnabled: false });
      if (!busy && !commandMode) setStatus("Ready", "idle");
      return;
    }
    if (!SpeechRecognition) {
      wakeToggle.checked = false;
      wakeEnabled = false;
      wakeHint.hidden = true;
      showError(
        "Hey Sabrah needs Chrome or Edge (Web Speech API). Use Start Talking instead."
      );
      return;
    }
    showError("");
    restoreIdle();
    startWakeListening();
  }

  talkBtn.addEventListener("click", () => {
    autoListenAfterReply = false;
    startListening({ followUp: false });
  });

  stopBtn.addEventListener("click", () => {
    stopListening({ discard: false });
  });

  muteBtn.addEventListener("click", () => {
    muted = !muted;
    muteBtn.textContent = muted ? "Unmute" : "Mute";
    if (muted) stopPlayback();
  });

  resetBtn.addEventListener("click", async () => {
    stopPlayback();
    stopListening({ discard: true });
    transcript.innerHTML = "";
    clearOfferings();
    showError("");
    autoListenAfterReply = false;
    try {
      if (sessionId) {
        await fetch(`/api/v1/sessions/${sessionId}/reset`, { method: "POST" });
      } else {
        await ensureSession();
      }
      restoreIdle();
      if (wakeEnabled) startWakeListening();
    } catch {
      sessionId = null;
      showError("Could not reset conversation.");
    }
  });

  sendTextBtn.addEventListener("click", () => {
    const value = textInput.value.trim();
    textInput.value = "";
    autoListenAfterReply = false;
    sendText(value);
  });

  textInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const value = textInput.value.trim();
      textInput.value = "";
      autoListenAfterReply = false;
      sendText(value);
    }
  });

  wakeToggle.addEventListener("change", () => {
    enableWakeMode(wakeToggle.checked);
  });

  uploadTravelersBtn.addEventListener("click", async () => {
    const file = travelerFile?.files?.[0];
    if (!file) {
      if (uploadStatus) {
        uploadStatus.hidden = false;
        uploadStatus.textContent = "Choose an .xlsx or .csv file first.";
      }
      return;
    }
    try {
      await ensureSession();
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`/api/v1/sessions/${sessionId}/travelers/upload`, {
        method: "POST",
        body: form,
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || "Upload failed");
      if (uploadStatus) {
        uploadStatus.hidden = false;
        uploadStatus.textContent = payload.message || `Uploaded ${payload.count} travelers.`;
      }
      if (payload.travelers) {
        renderOfferings({ travelers: payload.travelers });
      }
      autoListenAfterReply = false;
      await sendText(`I uploaded ${payload.count} travelers from the file.`);
    } catch (err) {
      if (uploadStatus) {
        uploadStatus.hidden = false;
        uploadStatus.textContent = err.message || "Upload failed.";
      }
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopWakeListening({ keepEnabled: true });
    } else if (wakeEnabled && !busy && !commandMode) {
      startWakeListening();
    }
  });

  checkHealth();
  ensureSession().catch(() => {
    showError("Could not create a conversation session.");
  });

  enableWakeMode(wakeToggle.checked);
  const unlockOnce = () => {
    if (wakeEnabled) startWakeListening();
    document.removeEventListener("click", unlockOnce);
    document.removeEventListener("keydown", unlockOnce);
  };
  document.addEventListener("click", unlockOnce);
  document.addEventListener("keydown", unlockOnce);
})();
