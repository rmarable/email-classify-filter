# Passphrase wordlist

`bip39-english.txt` is the BIP 39 English wordlist (2048 words, one per line), downloaded
unmodified on 2026-10-03 from
https://raw.githubusercontent.com/bitcoin/bips/master/bip-0039/english.txt
(last changed in commit `ce1862ac6bcffa1dd20aad858380e51e66e949ea`, 2014-02-07;
SHA-256 `2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda`).
ecf picks six words from it at random for the passphrase it offers for a manual export
(11 bits a word, 66 bits in all; SPEC §11.9, OD-326, OD-349).

BIP 39 states "License: MIT" in its header and "This BIP falls under the MIT License." in its
Copyright section (read 2026-10-03); see `LICENSE-BIP39.txt`.
