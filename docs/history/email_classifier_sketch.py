# Superseded by SPEC.md on 2026-09-27; kept for history. Not maintained.
# Moved here from the repository root; content below is unchanged (it still names mistral-small:7b).
"""
Rough sketch: local email classifier using Mistral via Ollama.
- Model runs locally (no cloud calls, no third-party sees email content)
- Output strictly constrained to a predefined JSON schema (Ollama's `format` param)
- Action step is a stub for now — swap in real logic (label/move/archive) later
"""

import json
import ollama
from pydantic import BaseModel, ValidationError
from typing import Literal


# --- 1. Predefine your schema ---------------------------------------------
# Start simple: category + priority. Add fields here later without touching
# the rest of the pipeline.

class EmailClassification(BaseModel):
    category: Literal["work", "personal", "marketing", "notification", "other"]
    priority: Literal["low", "medium", "high"]


# Ollama's `format` param wants a JSON schema, not a Pydantic class directly —
# this converts it for you.
SCHEMA_JSON = EmailClassification.model_json_schema()


# --- 2. Classification function ---------------------------------------------

MODEL_NAME = "mistral-small:7b"  # or your MLX equivalent

def classify_email(subject: str, sender: str, body: str) -> EmailClassification:
    prompt = f"""Classify the following email.

Definitions:
- work: related to your job
- personal: from people you know
- marketing: promotional emails, newsletters, sales
- notification: automated alerts (shipping, receipts, account activity)
- other: anything that doesn't fit above

Priority is "low" unless the email requires action or is time-sensitive.

From: {sender}
Subject: {subject}
Body: {body[:1500]}
"""

    response = ollama.chat(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        format=SCHEMA_JSON,  # <-- hard constraint: model can only emit matching JSON
    )

    raw = response["message"]["content"]

    try:
        return EmailClassification.model_validate_json(raw)
    except ValidationError as e:
        # Schema harness failed somehow (shouldn't happen often with `format`,
        # but don't trust blindly) — surface it rather than silently guessing.
        raise RuntimeError(f"Model returned invalid classification: {raw}\n{e}")


# --- 3. Action step (stub for now) ------------------------------------------

def take_action(subject: str, result: EmailClassification):
    # Replace this later with: apply Gmail label, move to folder, archive, etc.
    print(f"[{result.category} / {result.priority}] {subject}")


# --- 4. Pipeline -------------------------------------------------------------

def process_email(subject: str, sender: str, body: str):
    result = classify_email(subject, sender, body)
    take_action(subject, result)
    return result


# --- 5. Example run ------------------------------------------------------------

if __name__ == "__main__":
    sample_emails = [
        {
            "subject": "50% off today only!",
            "sender": "deals@someshoesite.com",
            "body": "Flash sale ends tonight. Shop now and save big on your next pair.",
        },
        {
            "subject": "Q3 budget review — need your input by Friday",
            "sender": "boss@yourcompany.com",
            "body": "Can you take a look at the attached spreadsheet before our sync?",
        },
    ]

    for email in sample_emails:
        process_email(**email)
