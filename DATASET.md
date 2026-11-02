# Dataset and Evaluation (Project Guide Section 2)

## Current implementation status

`membridge evaluate --output <new-folder>` now provides a small, reproducible
three-case synthetic content benchmark and controlled prompts for no-handoff,
manual-handoff and MemBridge-handoff conditions. It also creates a response template
for fresh receiving-agent answers. `--responses <filled-template>` scores a literal
phrase rubric that requires human review. See [CONTINUITY.md](CONTINUITY.md).
Content checks and character ratios are not live agent QA or token-reduction results.

The repository includes synthetic OpenCode (v2 and legacy), Codex, Claude Code, Gemini (JSON and JSONL), Aider, and universal DeepSeek/Grok transcript fixtures.
They support functional parser and handoff tests, not a statistically meaningful quality benchmark.
The automated suite also exercises the compatible provider contract with mocked responses and
the complete service flow over real localhost HTTP using the extractive baseline.
No live-model extraction scores or fresh-Codex QA accuracy are claimed yet. The larger labeled
dataset and comparisons below remain the evaluation plan. Split related sessions by project/task
to prevent closely related context from leaking between tuning and held-out sets.

The `evaluate-live` harness now records real OpenCode/Codex CLI stages and assistant
recall in an isolated synthetic project. A successful small round trip establishes
that tested workflow only; it does not replace the larger held-out evaluation below.
Stage failures and timeouts remain in the report. OpenCode receiver scoring excludes
tool output so merely reading an answer from a file cannot count as a correct final answer.

## Sources
1. **Real sessions (anonymised)**: exports from team members' own CLI usage. Strip secrets and private code before committing.
2. **Synthetic sessions**: generate multi-turn coding conversations with an LLM across varied projects (web app, data pipeline, bug fix, refactor), in each supported CLI's format, so every adapter has test data.
3. **Public coding-chat corpora** (optional): check licences before use.

## Suggested size
~50-100 sessions across 3+ tools, with varied length (short fixes to long multi-hour sessions).

## Labels for evaluation
For each session, write (or generate then manually verify):
- Ground-truth key decisions, TODOs, files changed.
- 5 follow-up questions whose answers depend on earlier context.

## Evaluation plan
| Metric | How |
|---|---|
| Memory extraction quality | Precision/recall vs ground-truth decisions/TODOs |
| Handoff usefulness | Ask follow-up questions to a fresh agent **with vs without** the brief; compare answer accuracy |
| Retrieval quality | Top-k hit rate of the correct memory for a query |
| Compression | Brief tokens / full transcript tokens |
| Adapter coverage | Number of CLIs parsed with zero errors |

## Train/test
Hold out ~20% of sessions as a test set; tune prompts and retrieval on the rest.

## Data hygiene
Document redaction steps and note them under limitations in the README.
