# Settings Page — Content & Structure

## Page header
- Title: "Settings"
- Back button: returns to Profile

---

## Section 1 — Recommendations
*Primary personalization lever. Placed first because it's the setting users are most likely to return to.*

**How Picky Are You?**
Three mutually exclusive options (radio select):

| Option | Subtitle |
|--------|----------|
| Picky | You don't settle |
| Balanced | You keep things measured |
| Bold | You're willing to take chances |

Maps to threshold presets: Picky (strong: 0.90, maybe: 0.60), Balanced (strong: 0.85, maybe: 0.55), Bold (strong: 0.80, maybe: 0.50).

---

## Section 2 — Reference Library
*Library management. Less frequent than pickiness but more consequential.*

**Status line** (read-only)
- If no library: "No library uploaded yet"
- If library exists: "312 books · Last updated 4 May 2026"

**Update Reference Library** (single action)
Opens a bottom sheet with two paths:

- **Add new books** — append books from a new CSV to your existing library. Use this when you've read new books since your last upload.
- **Start over** — wipe your current library and rebuild from a new CSV. Styled as a destructive/warning action.

---

## Section 3 — Account
*Identity and auth. Destructive actions are scoped here.*

**Signed in as**
- Icon: account_circle
- Label: auth method (e.g. "Signed in with Apple" / "Signed in with Google" / "Signed in with email")
- Sub-label: email address

**Sign Out**
- Standard action row

**Delete Account**
- Destructive action row (muted warning color)
- Tapping opens confirmation bottom sheet: explains permanent deletion of account, library, and scan history. Requires explicit confirmation tap.

---

## Section 4 — About
*Single tappable row that opens the About bottom sheet.*

**About Inkling**
- Icon: info
- Opens bottom sheet containing: logo/wordmark animation, version number, tagline, description, copyright

---

## Footer
*Legal and support. Minimal — just links.*

- Get Help (link)
- Privacy Policy (link)
- Terms of Use (link)
- Version number (e.g. "Inkling v1.0.0") — tapping opens About sheet as an alternative entry point

---

## Bottom sheets referenced

### Update Reference Library sheet
Trigger: tapping "Update Reference Library"
Options:
1. **Add new books** — subtitle: "Upload a new CSV to add books since your last upload"
2. **Start over** — subtitle: "Wipe and rebuild your library from a new CSV" — destructive styling

### Delete Account confirmation sheet
Trigger: tapping "Delete Account"
Copy: "This will permanently delete your account, library, and scan history. This can't be undone."
Actions: "Delete Account" (destructive), "Cancel"

### About sheet
Trigger: tapping "About Inkling" row or version number in footer
Content: logo animation, version, tagline ("To read, or not to read? That is the question."), description, copyright
