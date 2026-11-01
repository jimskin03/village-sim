# Survival in Lantern Bay

The village's goal is to keep everyone alive. Survival is always enabled for
1–8 present residents. Visitors do not count or consume rations. Each resident
starts with food, water and health at 100, with all three bounded to 0–100.

One step is 15 game minutes; 96 steps make a day. Rates are deterministic and
independent of real-time playback speed.

| Rule | Per step or ration |
| --- | --- |
| Food and water drain | 0.5 each, including while collapsed |
| Fish ration | +40 food |
| Herb bundle | +15 food |
| Drink | 1 shared water unit gives +20 water |
| Food or water below 25 | −1 health |
| Food or water at zero | −2 health, replacing the −1 rate |
| Both food and water at least 60 | +0.5 health |
| Other health conditions | No change |

Health is calculated after drain. Low health (below 30) makes the resident tired
and favors food, water and rest. Rest by itself does not restore health.

## Collection and cooperation

The **food collection center** holds the shared fish and herbs. Fishing and
gardening results are deposited there automatically, following the existing
pantry work mechanic. Supper at 18:00 uses one fish per resident and one herb
bundle per three residents (rounded up). Each bundle feeds one person. Rations
go to the hungriest residents first, with resident ID breaking ties. An empty
pantry gives no refill and leaves the usual shortage memories and goal hints.
Collapsed residents cannot eat; their ration stays in the center. Hungry
residents can also take a snack at the center or hearth through an `eat` activity.

The **water collection center** holds a separate shared water store. It starts
with three units per initial resident. A `collect water` activity at the well or
rain barrel transfers up to six available units into this store. A `drink`
activity at either source or the center takes one step after arrival and uses
one unit from the shared store. The well starts with six units and replenishes
one per step. The barrel starts with six units and replenishes one per step in
clear weather or three during a storm. Neither source has a maximum stock,
daily yield cap or midnight reset. Empty sources require a short wait. Seawater
at the shore or jetty is rejected explicitly.

Every observation and snapshot includes `villager_count`, `present_villagers`,
`collection_centers`, `water_sources`, and each resident's stats. The roster
includes the observer and collapsed residents, excludes visitors and absent
residents, and reports who is busy. Food's one-day threshold is 48 food points
per resident; water's is `ceil(48 × resident_count / 20)` units.

The rule layer responds to thirst at 60 and hunger at 55. If no food remains
and food is at most 40, anyone can gather emergency herbs, including at night
or during a storm. Free residents who have completed their initial craft goal
compare the centers with daily demand. They rank by distance to usable sources
or gathering places, then resident ID, and divide chores according to the
deficit. Water is assigned first and remaining helpers gather food. Shared
chores leave the midday social window free; personal needs still take priority.
No survival task is reserved for a craft. Hybrid plans offer `eat`, `drink`,
`collect_water`, and fishing/gardening work for every resident. Personal needs
interrupt plans and conversations so a long meeting cannot prevent drinking.

## Collapse and recovery

Health zero causes a collapse. Five game minutes round up to one 15-minute
step: the resident stays where they fell for that entire tick and recovers at
the start of the next tick with health 10. Food and water continue draining.
Health stays at zero during collapse; the recovery tick preserves restart
health before ordinary health changes resume on subsequent ticks.

A collapsed resident cannot move, work, eat, drink, speak, attend meetings,
submit model actions or accept visitor nudges. Other residents and visitors
cannot change the collapse deadline. Supplies can still be gifted to the
centers. There is no immunity window: unresolved needs can cause another
collapse. Every collapse and recovery has its own journal event and memory.
Residents never die, leave or become permanently disabled from these stats.

## Visitors, saves and replay

Visitors can gift one or two fish or herb units at the food center, and water
at the water center, well or barrel. Existing jetty/garden/hearth food gifts
remain available. `foul_barrel` temporarily blocks barrel collection and
drinking there for four steps; replenishment continues and the well stays
usable. Repeated sabotage within that window cannot extend its deadline.
All effects share the existing six-unit daily visitor budget and retain their
event provenance and ledger entries.

Checkpoint schema 8 persists stats, collapse deadlines, source supplies,
temporary fouling and shared water. Schemas 1–7 migrate without changing the
input document; all residents, including absent ones, receive starting stats
and the new places are added to the old map. New checkpoints validate bounds
and reject nonfinite numbers. Replay/snapshot envelopes stay at schema 1 with
additional survival fields, so old stories remain viewable without meters.
