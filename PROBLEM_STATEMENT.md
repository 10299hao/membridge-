# Problem Statement and Relevance (Project Guide Section 1)

**Primary domain:** Data Engineering & Analytics
**Secondary:** Automation, Machine Learning

## Problem statement
AI coding assistants run as separate CLIs, each storing conversation history in a different, private format. When an engineer switches tools mid-task (rate limits, cost, model strengths, team standards), all accumulated context is lost, forcing them to re-explain the project, re-discover decisions, and repeat work. There is no tool-agnostic, AI-curated memory layer that follows the developer across CLIs.

## Why it matters
- Engineers increasingly use multiple AI agents in one workflow.
- Lost context wastes time and tokens, and causes inconsistent decisions between tools.
- Raw transcripts are too long to paste; they need summarising and selective retrieval.
- Vendor lock-in: history tied to one tool discourages trying others.

## Objectives
1. Normalise sessions from multiple CLIs into one schema.
2. Extract structured memory with AI (decisions, files, TODOs, preferences).
3. Retrieve relevant memory semantically and inject it into the next CLI.
4. Make adding a new CLI a small plugin task.
5. Deliver as scalable, containerised microservices on Kubernetes.

## Intended users / customers
- Individual developers using 2+ AI CLIs.
- Students and small teams who want to share project context across tools.
- Teams that want a searchable history of AI-assisted decisions.

## Originality
Existing features are per-tool (e.g. a single CLI's own memory file). MemBridge is **cross-tool, adapter-based, and AI-curated**.

## Success measures
- Handoff QA accuracy: agent answers follow-up questions correctly with memory vs without (see DATASET.md).
- Token reduction: brief size vs full transcript.
- Time to add a new adapter.
