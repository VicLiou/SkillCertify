---
name: example-skill
description: A trivial demo skill that greets a name and writes the result to a file. Use when asked to produce a greeting file.
---

# Example skill

When asked to greet someone:

1. Run `scripts/greet.py <name>` to generate the greeting text.
2. Write the greeting into `out.txt` in the current working directory.
3. Report in your final message how many characters were written, ending with the word `done`.

See `references/style.md` for the required greeting tone.
