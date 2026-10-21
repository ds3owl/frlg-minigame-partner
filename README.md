# FRLG Minigame Partner

Get the minigame star on your Trainer Card in Pokémon FireRed and LeafGreen on Nintendo Switch, even on your own.

The star needs both records: at least 200 jumps in a row in Pokémon Jump and at least 200 berries in one game of Dodrio Berry Picking. Both are local wireless multiplayer minigames, and it's hard to find people who play the Switch version. Here the PC fills the other player slots, and you play your own Pokémon. Your save is never edited; the game itself saves your records.

I took PokeLDN, removed all of its trading, gift and editing features, kept only the communication part, and built Pokémon Jump and Dodrio Berry Picking on top of it.

## What you can do

- Pokémon Jump (2 players): when the player becomes the leader, the PC (Ash) joins.
- Dodrio Berry Picking (3 players): when the PC (Ash) opens a group, the player joins it.

## The PC

- Pokémon Jump: it didn't miss up to 9999 jumps in testing, and up to 205 on real hardware.
- Dodrio Berry Picking: it never takes a berry the player can pick.

## What you need

- A Windows PC
- An ESP32 board (classic ESP32, e.g. ESP32-DevKitC) and a USB cable
- A Switch key file (`prod.keys`). It isn't included in the program.
- An official copy of FireRed or LeafGreen for Switch

## How to use

1. Download `FRLG-Minigame-Partner.exe` from [Releases](../../releases) and run it. If "Windows protected your PC" appears, click **More info → Run anyway**.
2. Then follow the steps in the program.

You can also operate it with an AI tool (Claude Code, etc.). Download **Source code (zip)** from Releases and hand it to the AI tool.

## Caution

- If the wireless connection drops now and then, the PC's Pokémon may get hit at that moment.
- If the board's port doesn't show up, get the CP210x driver from Windows Update.

## FAQ

### How do I get the minigame star on my Trainer Card?

Set both records: at least 200 jumps in a row in Pokémon Jump and at least 200 berries in one game of Dodrio Berry Picking. Together, these records earn one Trainer Card star.

### Can I play Pokémon Jump or Dodrio Berry Picking solo on Switch?

Yes. You need one Switch, one Windows PC and a USB-connected ESP32 board. The PC fills the other player slots as Ash. In Pokémon Jump, you lead and the PC joins. In Dodrio Berry Picking, the PC opens a three-player group and you join. You still play your own Pokémon.

## License

AGPL-3.0. The communication part is adapted from [PokeLDN](https://github.com/Decryptu/pokeldn) (AGPL-3.0), and the ESP32 firmware is PokeLDN's, unmodified.

This is a non-commercial fan project. I don't sell it, and I won't accept donations or any money for it.

Unofficial fan project, not affiliated with Nintendo or The Pokémon Company. Pokémon and Nintendo Switch are trademarks of their owners; under section 7(e) of the AGPL, no right to use them is granted.
