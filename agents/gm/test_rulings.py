"""Checks the filter that stops the game master acting for a player. No model or site needed.
Usage: python agents/gm/test_rulings.py"""
import os, sys, tempfile
from pathlib import Path

os.environ.setdefault("GM_STATE_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gm

fails = 0
names = gm.player_names("observant-otter", "Observant Otter")
WATCHING = "I do not declare myself a faction. I stand at the edge and watch the guard, and I would ask him what he is protecting."
BACKING = "I step back out of the crowd's reach and keep my hands visible. I do not draw anything."


def check(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg); fails += 0 if cond else 1


def flags(text, move=""):
    return gm.acts_for_player(text, names, move)


# The rulings the live game actually produced.
REAL_1 = ("Observant Otter observes the checkpoint with the sharp awareness of an outsider. The woman's desperation hangs in the air like smoke from a funeral pyre, "
          "while the hooded figures move with the deadly silence of apex predators stalking prey. The scarred guard remains impassive as he delivers his refusal. "
          "In this moment, Otter realizes that their presence here means nothing to anyone else, that they are just another observer in a world where observation is its own punishment. "
          "The woman shuffles away with defeat etched into every line of her face.")
REAL_2 = ("Observant Otter's approach is swift and silent, cutting through the crowd to reach the hooded figures just as their leader's hand closes around the merchant's satchel. "
          "The leader turns with cold eyes, but before he can react, Otter speaks in the old tongue of the scholar-priests - a language that predates the shard wars entirely. "
          "'The satchel, or the man? Be precise.' The merchant's face shifts subtly, and Otter realizes this is no simple thief-and-victim scenario.")
print("Caught: speech, thoughts and feelings (always)")
a1, a2 = flags(REAL_1, WATCHING), flags(REAL_2, WATCHING)
check(any("realizes" in x for x in a1), "scene 1: 'Otter realizes that their presence here means nothing'")
check(any("approach is swift" in x for x in a2) and any("speaks in the old tongue" in x for x in a2), "scene 2: an invented approach and invented speech")
for text in ("@observant-otter says, 'Stand aside.'", "Otter quietly decides to follow them.", "Observant Otter's voice carries across the square.",
             "The guard laughs. observant otter feels a cold certainty settle in.", "Then @observant-otter suddenly shouts a warning.",
             "You realize the merchant has been lying all along.", "The crowd's surge stutters as your words hang in the stale air."):
    check(bool(flags(text, WATCHING)), f"caught: {text}")

print("Caught: actions the player did not write")
for text in ("You draw your blade and lunge at the leader.", "Otter strides through the crowd and seizes the satchel.",
             "The crowd's rage proves too overwhelming for your calculated withdrawal.", "As the mob closes in, you vault the stall and run for the gate."):
    check(bool(flags(text, WATCHING)), f"caught: {text}")

print("Allowed: what happens to the character, and what it perceives")
for text in ("A woman's rusty blade catches the afternoon light as it swings toward your shoulder.",
             "A woman's boot crashes into your ribs, sending you sprawling across the wet cobblestones as the crowd surges forward.",
             "The mob surges past where you stood, their fists and clubs swinging in all directions.",
             "You see the merchant slide a second blade into his sleeve. You hear the gate bar drop.",
             "A hulking man with a makeshift club swings at you. You stumble, and the crowd closes over the gap.",
             "The guard does not look up. Whatever @observant-otter was hoping to learn stays behind his teeth.",
             "'Move along,' the guard says to @observant-otter. 'You have no business here.' The woman is already gone.",
             "The leader realizes he is being watched and decides to wait. Nobody speaks to the otter-faced carving above the gate.",
             "A child asks Observant Otter for a coin, and the crowd closes in before any answer is possible."):
    check(not flags(text, WATCHING), f"allowed: {text[:74]}")

print("Allowed: restating what the player wrote")
check(not flags("A wooden club whistles past your head as you step backward. The old man swings again.", BACKING), "the player wrote 'I step back', so 'as you step backward' is a restatement")
check(bool(flags("A wooden club whistles past your head as you step backward.", WATCHING)), "the same line is caught when the player only watched")
check(not flags("@observant-otter watches the guard. He gives nothing away.", WATCHING), "'@observant-otter watches' restates the move")

print("Last resort")
cut = gm.strip_acted(REAL_1, names, "fails", "observant-otter", WATCHING)
check("realizes" not in cut and "shuffles away" in cut and gm.words(cut) > 20, "offending sentence is cut, the rest of the ruling is kept")
bare = gm.strip_acted("Otter decides to leave.", names, "fails", "observant-otter", WATCHING)
check(bare == "What @observant-otter attempted does not work.", "if nothing is left, the bare result is stated")
print("\nall good" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
