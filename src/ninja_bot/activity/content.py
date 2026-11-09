"""Intro and roast lines for the monthly overview (constants only).

`{user}` is a mention, `{hours}` the month's voice time (e.g. `103h 07m`).
"""

from __future__ import annotations

INTRO_LINES: tuple[str, ...] = (
    "Overview of the month! 🎉",
    "Overview of the month, let's see who touched no grass. 🌱",
    "It's overview of the month time! 📊",
    "Overview of the month. Brace yourselves. 🫡",
    "Drumroll please... overview of the month! 🥁",
    "Overview of the month! The receipts are in. 🧾",
    "Here's the overview of the month, no refunds. 📈",
    "Overview of the month. I've been watching. 👀",
)

ROAST_FIRST: tuple[str, ...] = (
    "🥇 {user} spent **{hours}** in voice. Do you even own a window?",
    "🥇 {user}: **{hours}**. That's not a hobby, that's a lease.",
    "🥇 {user} clocked **{hours}** in voice. Touch grass, I'm begging.",
    "🥇 {user} with **{hours}**. Your family called, they miss you.",
    "🥇 {user}, **{hours}** in voice?! Go outside, the graphics are amazing.",
    "🥇 {user} put in **{hours}**. Employers wish they had this dedication.",
    "🥇 {user}: **{hours}**. Vitamin D is a real thing, you know.",
    "🥇 {user} lives here now. **{hours}** in voice. We'll forward your mail.",
    "🥇 {user} racked up **{hours}**. Absolutely no life, truly inspiring.",
    "🥇 {user}, **{hours}**. The sun is a free-to-play game, try it.",
)

ROAST_SECOND: tuple[str, ...] = (
    "🥈 {user} with **{hours}**. Second place, and still no life.",
    "🥈 {user}: **{hours}**. So close to the crown, so far from fresh air.",
    "🥈 {user} managed **{hours}**. Not even the best at having no life.",
    "🥈 {user} clocked **{hours}**. Silver medal in sitting inside.",
    "🥈 {user}, **{hours}** in voice. Your chair has a permanent dent.",
    "🥈 {user} put in **{hours}**. Runner-up at wasting a month, congrats.",
    "🥈 {user}: **{hours}**. Do you have plans? Any? Ever?",
    "🥈 {user} with **{hours}**. Almost a full-time job, minus the pay.",
    "🥈 {user} spent **{hours}** in voice. Go pet a dog or something.",
    "🥈 {user}, **{hours}**. We see you, and we're a little worried.",
)

ROAST_THIRD: tuple[str, ...] = (
    "🥉 {user} with **{hours}**. Bronze in having no life, still on the podium.",
    "🥉 {user}: **{hours}**. Third place, and not a single sunbeam in sight.",
    "🥉 {user} clocked **{hours}**. Even the bronze needs a hobby.",
    "🥉 {user}, **{hours}** in voice. A respectable amount of nothing else.",
    "🥉 {user} put in **{hours}**. Barely made the podium, completely made the couch.",
    "🥉 {user}: **{hours}**. Tell your friends outside hi.",
    "🥉 {user} with **{hours}**. Participation trophy for staying indoors.",
    "🥉 {user} spent **{hours}** in voice. Could've learned a language by now.",
    "🥉 {user}, **{hours}**. Top 3 and zero shame, love that for you.",
    "🥉 {user} managed **{hours}**. Hope the snacks were worth it.",
)
