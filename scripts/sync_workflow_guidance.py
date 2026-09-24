"""Keep the common host loop identical across public distribution assets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/workflow-host-loop.md"
TARGETS = ("docs/agent-onboarding.md", "skills/codex-job-agent/SKILL.md",
           "skills/claude-code/SKILL.md", "skills/openclaw-job-agent/SKILL.md",
           "src/jobagent/data/codex_skill/SKILL.md")


def sync():
    section = SOURCE.read_text(encoding="utf-8").rstrip() + "\n\n"
    for name in TARGETS:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        start = text.index("## Host-independent workflow contract\n")
        end = text.index("## Installation-to-account handoff", start)
        path.write_text(text[:start] + section + text[end:], encoding="utf-8")


if __name__ == "__main__":
    sync()
