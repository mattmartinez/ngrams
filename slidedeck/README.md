# slidedeck

Self-contained HTML slide deck generator. Produces a single `.html` file you open in a browser — no build tools, no dependencies.

## Usage

```
/slidedeck <what you want + any context>
```

The skill pulls from your current conversation, so feed it source material first (docs, findings, code, audit notes), then call it with the narrative direction.

## Examples

### From conversation context

Have a discussion, do an audit, trace some code — then turn it into slides:

```
/slidedeck Kinesis elimination proposal — how it works now, why it's bad, what we propose, migration plan
```

```
/slidedeck QuestDB migration where it started, where we are, where it's headed (prod)
```

```
/slidedeck summary of today's RDS SSL findings for the team standup tomorrow
```

### With emphasis points

Tell it what to hit hard:

```
/slidedeck exchange-reporter refactor — emphasize: 1) current complexity 2) cost savings 3) phased rollout with rollback at every step
```

```
/slidedeck Terraform state split proposal — focus on blast radius reduction and the fact that nothing changes for devs day-to-day
```

### With a specific output path

```
/slidedeck Jenkins pipeline redesign to ./docs/jenkins-redesign-deck.html
```

```
/slidedeck cost basis engine overview to ~/Desktop/cost-basis-deck.html
```

### Cold start (no prior conversation)

Point it at source material directly:

```
read docs/architecture.md and the CONCERNS.md — then /slidedeck architecture overview for new engineers joining the team
```

```
read the last 3 PR descriptions on node40-api — /slidedeck what we shipped this sprint
```

## Tips

- **Narrative order matters.** "how it works, why it's bad, what we propose" gives a story arc. "overview of the system" gives a flat list.
- **Call out the audience** if it's not obvious — "for engineering leadership" vs "for the team implementing this" changes tone and detail level.
- **Include quotable principles** if someone said something sharp — e.g. "emphasize: the owner of the data is the producer" becomes a dedicated slide.
- **Iterate after.** Ask for changes: "swap slides 4 and 5", "add a cost comparison slide after the architecture section", "the flow diagram should show the error path too".

## Output

Writes to `local/<descriptor>-deck.html` by default, or wherever you specify. Opens automatically in your browser.

Navigate with arrow keys, spacebar, or the prev/next buttons. Swipe works on mobile.
