# Genko SDK — coding agent prompt

Copy from here for your landing page or agent chat. Full integration steps:
[`AGENT_INTEGRATION.md`](AGENT_INTEGRATION.md).

## Coding agent prompt

Paste into a coding agent, such as Claude Code, Codex, or Cursor:

```text
Ensure Python 3.10+ and FastAPI are in this project.

Install Genko Skills:
git clone --depth 1 https://github.com/haiku-cursor-hackaton/python-sdk.git .genko-sdk && mkdir -p .cursor/skills && cp -r .genko-sdk/.cursor/skills/wire-genko-sdk .cursor/skills/

Then read https://raw.githubusercontent.com/haiku-cursor-hackaton/python-sdk/main/docs/AGENT_INTEGRATION.md and wire UCP into this store.
```

## From the command line

```bash
git clone https://github.com/haiku-cursor-hackaton/python-sdk.git
cd your-store
pip install -e ../python-sdk
mkdir -p .cursor/skills
cp -r ../python-sdk/.cursor/skills/wire-genko-sdk .cursor/skills/
```

Follow [`AGENT_INTEGRATION.md`](AGENT_INTEGRATION.md) in your store repo.

Adds `/.well-known/ucp` and `/ucp/v1/*` to your FastAPI store, with the
**wire-genko-sdk** agent skill.
