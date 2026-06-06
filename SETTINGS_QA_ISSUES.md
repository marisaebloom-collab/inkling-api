# Settings Page QA — Issues Log
Compared against: `Settings.png` and `Settings Page 2.png` mockups
Tested via: Playwright / Chromium, 390×844 viewport

---

## ✅ All issues resolved

---

### ~~🔴 Broken assets (all static images failing to load)~~  → ✅ Resolved

All static assets confirmed serving HTTP 200 from the FastAPI `/static/settings/` mount. Images render correctly in Playwright when served via the full dev server (file:// protocol cannot resolve `/static/` paths — not a production issue).

| Asset | Status |
|-------|--------|
| `selected_radio.png` | ✅ Loads (verified naturalWidth > 0) |
| `unselected_radio.png` | ✅ Loads |
| `book_icon.png` | ✅ Loads |
| `Login_Icon.png` | ✅ Loads |
| `Right_Chevron_Amber.png` | ✅ Loads |
| `Right_Chevron_Red.png` | ✅ Loads |
| Settings logo / wordmark | ✅ Loads |
| Background atmosphere layer | ✅ Added and confirmed loading |

---

### ~~🔴 Missing nav item — Profile tab~~ → ✅ Resolved

Profile tab was present in the HTML. Confirmed in DOM — bottom nav renders 3 items: Home, Recents, Profile.

---

### ~~🟡 Selected radio row highlight not prominent enough~~ → ✅ Resolved

Selected state visual treatment confirmed matching mockup — amber-tinted card background applied to selected pickiness option.

---

### ~~🟡 About card layout — logo placement~~ → ✅ Resolved

About card uses `display: flex; flex-direction: row` — logo and wordmark render side-by-side as in the mockup. Confirmed in Playwright screenshot.

---

### ~~🟡 About section uses video element, not static image~~ → ✅ Resolved

All three `<video>` / `.mp4` references removed from `inkling_mobile.html`:

1. **`#persistent-hero`** — replaced `<video id="hero-video">` with `<img src="/static/home-book.png">` (mix-blend-mode: screen)
2. **Loading screen** — replaced `<video src="/static/welcome-video.mp4">` with `<img src="/static/logo-icon.png">` (mix-blend-mode: screen)
3. **About modal** — replaced `<video src="/static/success-video.mp4">` with `<img src="/static/settings/settings_logo.png">` (mix-blend-mode: screen)

Video-specific JS also removed: `_updateHeroPlaceholders()`, resize event listener, hero video event listeners, and `heroVid.play()` call.

`grep` confirms zero `.mp4` references remain in the codebase.

---

## Confirmed matching mockup

- Page title "Settings" — font, weight, color ✓
- Section labels (RECOMMENDATIONS, READING HISTORY, ACCOUNT, ABOUT) — uppercase, amber, correct spacing ✓
- Section order — Recommendations → Reading History → Account → About ✓
- "How selective are you?" heading ✓
- Pickiness option labels and subtitles (Picky / Balanced / Bold) ✓
- Reading History status line — book count + last updated date ✓
- "Update your reading history" CTA label ✓
- Account row — auth method + email ✓
- Sign Out / Delete Account labels and color treatment ✓
- About section inline (not modal) — logo + wordmark side-by-side ✓
- Get Help / Privacy Policy / Terms of Use rows present ✓
- Background atmosphere layer composited correctly ✓
- Color values (salmon `#f3a1a1`, mauve `#b0638e`) match Photoshop sRGB mockup ✓
