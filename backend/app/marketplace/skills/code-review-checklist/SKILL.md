---
name: code-review-checklist
description: Reviews a pasted code snippet or diff against a consistent checklist covering correctness, readability, and common bug patterns. Use when the user pastes code and asks for a review, feedback, or "does this look right".
---

# Code Review Checklist

## Instructions

When the user pastes code (a snippet, function, or diff) and asks for review or feedback, check it against:

1. **Correctness** — does the logic actually do what it appears intended to do? Look specifically for off-by-one errors, wrong comparison operators, inverted conditionals, and edge cases (empty input, null/None, zero, negative numbers) that aren't handled.
2. **Error handling** — are failure paths (a network call, a file read, a parse) handled, or will they crash or fail silently?
3. **Readability** — are names clear? Is there dead code, commented-out code, or unnecessary complexity that could be simplified?
4. **Obvious security issues** — unsanitized input reaching a shell command, SQL query, or file path; secrets hardcoded in the source.

## Output format

- Lead with whether the code looks correct overall in one sentence.
- List concrete issues found, each with the specific line or snippet it applies to and why it's a problem — not vague generalities.
- If nothing significant is wrong, say so plainly rather than inventing minor nitpicks to seem thorough.
- Don't rewrite the whole thing unless asked — point out the issues and let the user decide how to fix them, unless they specifically ask for a corrected version.
