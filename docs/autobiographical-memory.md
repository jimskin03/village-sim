# Villager autobiographical memory

Lantern Bay includes eight proposed fictional life histories in
[`village_sim/data/brains`](../village_sim/data/brains/). Each has 24
subjective recollections, five evidence-linked interpretations, recall routes,
and latent personal goals. Ren and Bea are available when the population grows
to include them.

## Import and privacy

New villages start with the histories enabled. A resident receives their own
copy when first created. For an older saved village, an operator can choose
**Import proposed histories** in Settings. This adds a separate biography
namespace for residents already present and enables it for residents who join
later. It does not alter live memories, beliefs, relationships, trust, goals,
events, or the replay journal. Turning histories off retains the imported data
in the checkpoint but stops recall; turning them back on resumes it.

An authorized villager observation and that villager's private conversation
context can contain only that resident's life history. Public snapshots,
replay frames, visitor observations, and the shared event journal do not contain
biography nodes. A history is marked as proposed and subjective. It can be
uncertain or mistaken; the current world and events the villager actually
experiences take precedence.

## Recall and behavior

`village_sim/cognition/autobiography.py` validates the authored pack and
retrieves scenes, interpretations, and latent goals from the current cue text.
A matching recall route also raises scenes on linked routes, scaled by the
link weight. Retrieval does not apply simulated-day recency decay. The planning
and conversation providers receive the selected items beside the existing
runtime memories. String biography IDs remain separate from numeric live-memory
IDs, so a plan cannot cite a fictional pre-simulation scene as a journal event.

A resident created while histories are enabled takes that history's seven trait
values. Importing histories into a village that already has those residents
does not change their traits, trust, beliefs, goals, live memories, or the
journal. Suggested trust from the history breaks a tie only when lived trust
and established friendships are equal. Lived trust still wins.

Without a provider, one relevant, evidence-backed facet can break a tie between
routine workplaces: jetty or shore for a fisher, the gardens for a gardener,
and the shore or net rack for a boatwright. The nudge applies only to the
ordinary craft activity. It is skipped during storms, festivals, urgent
survival needs, repair emergencies, and after four recorded outcomes at one of
those places, in either season. Four poor outcomes at the place a facet
preferred store a revision on that resident's private copy. The anchor text
stays as it was written. The facet's alternative becomes the current reading,
and the revision cites the live work events. Legal work and its result remain
controlled by the simulation. A history cannot change a catch, a repair, or
the pantry.

The autobiographical anchors are saved with checkpoints in their own namespace;
they are never inserted into `Agent.memory` or the event sequence. Existing
schema 1–10 checkpoints migrate with the feature off and no biography imported.
The pack copies live in each checkpoint after import, so a later source-file
change cannot silently rewrite a resident's recollections. If the source pack or
a saved biography namespace is malformed, the histories are disabled and the
village continues without them; Settings shows the diagnostic.

Run the pack's structural and chronology validator with:

```sh
python village_sim/data/brains/validate_brains.py
```

The source files are adapted from the issue's draft attachment and remain
proposed fictional histories. Repository-established facts and live simulation
events stay authoritative.
