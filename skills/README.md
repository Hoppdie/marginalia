# Marginalia skills

Three workflow-oriented skills for any LLM that drives the Marginalia
CLI (Claude Code, Cursor, etc.). Skills are progressive-disclosure
instructions — the agent loads the relevant one when the user's intent
matches the description.

## Layout

```
skills/
├── ingest-vault/SKILL.md           # bulk-load files into the db
├── research-with-marginalia/SKILL.md  # ask, follow citations, export
└── discover-and-curate/SKILL.md    # explore relations, build lists
```

## Installing into Claude Code

Either copy or symlink each directory into your Claude skills root:

```bash
# Linux/macOS
ln -s "$(pwd)/skills/ingest-vault" ~/.claude/skills/ingest-vault

# Windows (run as administrator for symlinks, or just copy the folder)
mklink /D "%USERPROFILE%\.claude\skills\ingest-vault" "%CD%\skills\ingest-vault"
```

Then re-launch Claude Code so it picks up the new skill descriptions.

## Why skills, not MCP

These skills drive the existing CLI — they don't expose new tools.
That keeps the surface tiny: each skill is one markdown file the agent
reads when relevant, no daemon, no protocol, no extra dependency. If
Marginalia later grows tools that need to be CALLED (rather than
INVOKED via the CLI), MCP remains an option.
