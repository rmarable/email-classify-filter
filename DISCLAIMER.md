# Disclaimer and acceptable use

ecf reduces the risk of email fraud. It does not remove it. This file says what ecf is not, what
you are responsible for, what you must not do with it, and what the author does not accept
responsibility for. It supplements [`LICENSE`](LICENSE) (Apache License 2.0, sections 7 and 8,
with the Commons Clause), which governs where the two differ.

## 1. Your use, your responsibility

You are responsible for:

1. every action you approve in Slack or the CLI, and every rule, policy and setting you apply;
2. having the right to read every mailbox you connect, including the informed consent of anyone
   else whose mail it holds;
3. what ecf sends to the services you enable (your mail provider, Slack and, in presets B and C,
   Anthropic), and your agreements with them (`SPEC.md` §12.4);
4. your own data protection and recovery, independent of ecf: backups of your mailboxes kept by
   your mail provider or your own tools, backups of ecf's data folder and exports, and a restore
   you have tested before you need it. ecf's export (`SPEC.md` §11.9) holds ecf's own state,
   never the messages themselves, and is not a substitute for a backup plan. ecf can move, label
   or delete mail; if that mail matters, keep a copy ecf cannot touch;
5. keeping ecf, its dependencies and its local model current;
6. any modified version of ecf you build, run or distribute.

## 2. Relying on ecf

1. **Classification is probabilistic.** The local model and Claude can be wrong, inconsistent,
   or influenced by text in the mail they read (prompt injection).
2. **The deterministic rules catch only what they encode.** New fraud patterns get through.
3. **Both produce errors.** False negatives (fraud or regulator mail not flagged) and false
   positives (legitimate mail flagged). Eval results in `SPEC.md` §16 describe synthetic test
   sets, not your mail.
4. **A flag, or the absence of one, is input to a decision, not the decision.** A person must
   review it and remains responsible for it. Verify every payment, bank-detail change and
   regulator request out of band, by a channel you already trust.
5. **ecf's guarantees are bounded.** v1 is single-user, macOS-only and assumes one trusted OS
   account. Its threat model and stated limits are in `SPEC.md` §12.1 and §12.2. Outside those
   bounds, assume nothing holds.
6. **ecf is not legal, financial, compliance or security advice,** and using it does not satisfy
   any regulatory, audit or contractual obligation.

## 3. Acceptable use

ecf is for protecting mailboxes you own or are authorised to watch. The author does not control,
endorse or accept responsibility for anything anyone else does with ecf, or with any modified
version, fork or derivative of it.

**Do not watch mail without authority.** This includes using ecf to:
- read, filter or act on a mailbox without the account holder's informed consent;
- monitor employees or anyone else beyond what applicable law permits;
- breach privacy, data-protection or interception law.

**Do not deceive or obstruct.** This includes using ecf to:
- hide, delay, alter or delete mail to deceive, defraud or obstruct anyone, including
  regulators, auditors and courts;
- destroy records you are required to keep.

**Do not attack others.** This includes using ecf, or modifying it, to:
- send phishing, spam, fraud or impersonation mail;
- design messages that evade ecf or other fraud detection, using its rules, signals or eval
  cards.

*Defensive security research, red-team testing of your own systems, and publishing evaluations of
ecf are not prohibited.*

Anyone who uses ecf in a prohibited way does so on their own initiative and at their own legal
risk, and is solely responsible for the consequences. Nothing in this repository authorises
unlawful use.

## 4. Third parties

ecf depends on services and software it does not control: mail providers, Slack, Ollama, local
and hosted models, and the packages in `THIRD_PARTY_NOTICES`. Their outages, changes, limits,
retention and errors are not the author's responsibility.

## 5–8. Warranty, liability and indemnity

These sections limit what you can claim from the author. They are in capitals so you see them.

## 5. Disclaimer of warranties

ECF IS PROVIDED "AS IS" AND "AS AVAILABLE", WITHOUT WARRANTY OF ANY KIND, EXPRESS, IMPLIED OR
STATUTORY, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE,
NON-INFRINGEMENT, ACCURACY, AND THAT ECF WILL DETECT ANY PARTICULAR MESSAGE, RUN WITHOUT
INTERRUPTION OR ERROR, OR KEEP DATA SECURE.

## 6. Limitation of liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, THE AUTHOR AND CONTRIBUTORS ARE NOT LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL OR EXEMPLARY DAMAGES, OR ANY LOSS, ARISING
FROM OR RELATED TO ECF OR ITS USE, MISUSE OR FAILURE, BY YOU OR ANYONE ELSE, INCLUDING MONEY PAID
TO A FRAUDSTER, MISSED OR LATE REGULATORY OR LEGAL DEADLINES, LOST, HIDDEN, MOVED OR DELETED
MAIL, DATA DISCLOSURE, DOWNTIME AND BUSINESS INTERRUPTION, UNDER ANY THEORY OF LIABILITY, EVEN IF
ADVISED OF THE POSSIBILITY OF SUCH DAMAGES AND EVEN IF THEY WERE FORESEEABLE.

## 7. Indemnity

TO THE MAXIMUM EXTENT PERMITTED BY LAW, YOU WILL INDEMNIFY AND HOLD HARMLESS THE AUTHOR AND
CONTRIBUTORS FROM ANY CLAIM, LOSS, LIABILITY OR EXPENSE (INCLUDING REASONABLE LEGAL FEES) ARISING
FROM YOUR USE OR MISUSE OF ECF, ANY MODIFIED VERSION OF IT YOU RUN OR DISTRIBUTE, OR YOUR BREACH
OF THIS FILE, `LICENSE` OR APPLICABLE LAW.

## 8. Where these limits don't apply

SOME JURISDICTIONS DO NOT ALLOW THE EXCLUSION OF CERTAIN WARRANTIES OR THE LIMITATION OF CERTAIN
LIABILITY, SO SOME OR ALL OF SECTIONS 5 TO 7 MAY NOT APPLY TO YOU. WHERE THEY DON'T, THEY APPLY
TO THE MAXIMUM EXTENT THE LAW ALLOWS.

## If in doubt

Don't approve it. Escalate to a person and verify independently.
