# Claude Code Skill — Job Agent

Lets Claude Code drive Job Agent CLI from natural-language prompts like "帮我找深圳的 AI 产品经理岗位".

## Install

```bash
mkdir -p ~/.claude/skills/job-agent
cp SKILL.md ~/.claude/skills/job-agent/SKILL.md
```

Verify in any Claude Code session:

```
/skills
```

You should see `job-agent` listed.

## Trigger

Mention any of these in chat:

- "找工作" / "投简历" / "Boss 直聘" / "打招呼"
- "简历分析" / "求职 agent" / "帮我看下我的简历"
- "match jobs" / "send greetings"

Claude Code will pick up the skill automatically and walk you through the workflow.

## Update

Whenever Job Agent's CLI surface changes (new commands, new flags), update [SKILL.md](./SKILL.md) and re-copy:

```bash
cp SKILL.md ~/.claude/skills/job-agent/SKILL.md
```

Future M2 work: have `jobagent init` install/update this skill automatically.
