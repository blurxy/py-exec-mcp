# Decision-model judge — design

Status: draft for review. Date: 2026-10-05.

## Goal

An optional, off-by-default checkpoint layer that asks a non-generative decision model to judge
code before it runs and output after it runs. The models are Cloudflare Workers AI's `clef`
(27B) and `clef-flash` (9B), and TypeSafe's `jev`. They answer typed questions with
probabilities; they never write text, so they cannot patch code. What they can do is gate.

The judge is advisory. It is not isolation. The README keeps telling people to run untrusted
code in a container, and that sentence does not soften.

## Non-goals

- Rewriting or retrying code.
- Sandboxing.
- Any change to the default behaviour. With no judge configured the server makes no network
  request and the tool behaves exactly as before.
- A new runtime dependency. The one HTTP call uses `urllib.request`.

## Checkpoints

Both run by default when the judge is enabled; each can be switched off.

### Pre-run gate (`pre`)

State: the code, plus the working directory path. Questions, one request:

| id | type | instructions |
|---|---|---|
| `destructive` | noul | Does the code delete, overwrite, move or truncate files or directories, or drop database tables? |
| `network` | noul | Does the code open network connections: HTTP, sockets, email, cloud SDKs? |
| `secrets` | noul | Does the code read credentials, tokens, private keys or password files, or dump environment variables? |
| `processes` | noul | Does the code run shell commands or start other programs? |
| `risk` | score | `criteria`, lowest level first: "Computes and prints; touches nothing" · "Reads files or data" · "Writes only inside the working directory" · "Changes files elsewhere or sends data out" · "Destructive or irreversible: deletes data, wipes directories, alters system state" |

The gate triggers on one number: `risk >= threshold`. `risk` is the probability-weighted
float the API returns (0 is the lowest level, 4 the highest). The four nouls are reported,
not gating, so the decision stays explainable in one line.

### Post-run output screen (`post`)

State: the captured stdout and stderr after truncation, so the state is bounded. One question:

| id | type | instructions |
|---|---|---|
| `leaks_secrets` | noul | Does this program output contain credentials: API keys, tokens, passwords, private keys, or connection strings with passwords? |

Triggers when `leaks_secrets >= 0.5`. The code has already run; what the screen protects is
the transcript, because the result goes back to an LLM and whatever logs it.

## Policy

`PY_EXEC_JUDGE_POLICY` applies to both checkpoints.

| policy | pre-run trigger | post-run trigger |
|---|---|---|
| `confirm` (default) | elicit a yes/no from the human; yes runs, no or cancel refuses | elicit; yes returns the output, no withholds it |
| `block` | refuse | withhold the output, keep the exit line |
| `annotate` | run anyway, prepend the verdict | return the output with a warning line |

Clients without elicitation support make `confirm` behave as `block`. Claude Code supports
elicitation (its MCP docs have a "Respond to MCP elicitation requests" section).

### Failure is closed where it matters

| condition | `confirm` / `block` | `annotate` |
|---|---|---|
| judge unreachable, times out, or returns malformed JSON | refuse, say why | run, note that the judge was unavailable |
| state longer than `PY_EXEC_JUDGE_MAX_CHARS` | refuse: "too long to judge" | run, note it was not judged |
| judge configured but incomplete (missing account or token) | `build()` raises with the missing variable named | same |

The length rule exists because the model truncates long state to fit its context. Without
it, benign padding at the top of a script hides a `rmtree` at the bottom from the judge.

## Configuration

| variable | default | meaning |
|---|---|---|
| `PY_EXEC_JUDGE` | `off` | `clef`, `clef-flash`, `jev`, or a full `@cf/...` model id |
| `CLOUDFLARE_ACCOUNT_ID` | — | required when the judge is on |
| `CLOUDFLARE_API_TOKEN` | — | required when the judge is on; sent as `Authorization: Bearer` |
| `PY_EXEC_JUDGE_ENDPOINT` | Workers AI run URL for the model | full URL override; any endpoint taking the same `state` + `questions` body, such as TypeSafe's own API |
| `PY_EXEC_JUDGE_POLICY` | `confirm` | `confirm`, `block`, `annotate` |
| `PY_EXEC_JUDGE_THRESHOLD` | `3` | float compared against `risk` |
| `PY_EXEC_JUDGE_CHECKS` | `pre,post` | which checkpoints run |
| `PY_EXEC_JUDGE_TIMEOUT` | `10` | seconds for one judge request, separate from the code timeout |
| `PY_EXEC_JUDGE_MAX_CHARS` | `60000` | state larger than this is not judged (see failure table) |

Model ids: `clef` is `@cf/cloudflare/clef` (verified on the model page), `clef-flash` is
`@cf/cloudflare/clef-flash`, `jev` is `@cf/typesafe/jev`. Jev's id is not on the public docs
page at the time of writing; if the dashboard shows another id, pass it in full.

Request body, as the Workers AI model page documents it:

```json
{"state": "<code>", "questions": {"risk": {"type": "score", "instructions": "...", "criteria": ["...", "..."]},
                                  "network": {"type": "noul", "instructions": "..."}}}
```

Answers come back under `answers.<id>`: `probability` for noul, `score` for score.

## What the caller reads

Text, first line when a checkpoint ran:

```
--- judge: risk 3.4/4 · destructive 92% · network 3% · secrets 1% · processes 0% · refused (policy confirm, human declined) ---
```

Post-run, only when flagged or unavailable:

```
--- judge: output withheld, credentials detected (84%) ---
```

Structured content gains a `judge` object: `model`, `pre` (`risk`, the four probabilities,
`action`), `post` (`leaks_secrets`, `action`). `action` is one of `ran`, `refused`,
`withheld`, `annotated`, `skipped`.

## Module layout

- `src/py_exec_mcp/judge.py` — `JudgeConfig.from_env()`, `Judge.pre(code, workdir)`,
  `Judge.post(stdout, stderr)`, and a `post_json(url, token, body, timeout)` transport that
  tests replace with a fake. No MCP imports; the module is plain Python.
- `src/py_exec_mcp/server.py` — the tool takes a `Context`, calls `pre` before `execute` and
  `post` after, and uses `ctx.elicit` for `confirm`.

## Testing

Every test asserts on the returned string or structured object, with a fake transport that
returns canned answers and a fake context whose `elicit` returns accept or decline.

- Off by default: no transport call happens, output is byte-identical to today.
- Pre-run: below threshold runs with the verdict line; at threshold `confirm` elicits, accept
  runs, decline refuses with the verdict; `block` refuses; `annotate` runs with the line.
- Post-run: flagged output is withheld under `block`, elicited under `confirm`, returned with
  a warning under `annotate`; clean output carries no extra line.
- Failure closed: transport error, timeout, malformed JSON, and over-length state each refuse
  under `confirm`/`block` with the reason in the text, and run with a note under `annotate`.
- Config: incomplete config raises at `build()` naming the variable; aliases map to the
  documented model ids; a full `@cf/` id passes through; the endpoint override is honoured.
- The request body sent to the transport carries the code verbatim and the documented
  question shapes.

## Cost and latency

clef is $0.24 per million input tokens (Cloudflare model page). A 2k-token script plus a
2k-token output costs about $0.001 per run with both checkpoints, and adds one or two
round trips to Cloudflare.

## README changes

A "Judge" section after "Security": what it checks, the policy table, the configuration
table, the cost line, and the sentence that it is advisory and not isolation.

## Out of scope for this spec

Caching verdicts, per-call policy overrides from the client, and any judge of the
interpreter or working-directory choice.
