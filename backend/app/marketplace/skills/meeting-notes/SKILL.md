---
name: meeting-notes
description: Turns raw meeting notes, transcripts, or a rambling recap into a clean structured summary. Use when the user pastes meeting notes, a transcript, or asks to summarize a meeting or call.
---

# Meeting Notes

## Instructions

When the user provides meeting notes, a transcript, or a recap and wants it summarized, structure the output as:

1. **Summary** — 2-3 sentences on what the meeting was about and its overall outcome.
2. **Key Decisions** — bullet list of anything that was explicitly decided or agreed. Omit this section if nothing was actually decided.
3. **Action Items** — bullet list in the form "[Owner if known] — [task]". If no owner is stated for an item, write "Unassigned —" rather than guessing who is responsible.
4. **Open Questions** — anything left unresolved or flagged for follow-up. Omit if there weren't any.

Rules:
- Do not invent decisions, owners, or action items that aren't actually in the source material — if the notes are vague, keep the summary vague rather than filling in plausible-sounding specifics.
- Keep it tight. This is a scan-in-ten-seconds summary, not a full recap.
- If the source is a raw transcript with speaker labels, use them to attribute action items and decisions accurately.
