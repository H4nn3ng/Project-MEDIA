from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types
from jsonschema import Draft7Validator

from config import CORPUS_PATH, GEMINI_PRO, get_secret
from core.schemas import ConceptCorpus, ConceptEntry, corpus_from_dict


BASE_DIR = CORPUS_PATH.parent.parent
EXAMPLES_PATH = BASE_DIR / "schemas" / "concept_corpus.examples.json"
SCHEMA_PATH = BASE_DIR / "schemas" / "concept_corpus.schema.json"
REVIEW_PATH = CORPUS_PATH.parent / "seed_review.txt"
SLUG_PATTERN = re.compile(r"^[a-z0-9_]+$")

SOURCE_BOOKS = [
    ("Thinking, Fast and Slow", "Kahneman"),
    ("The Psychology of Money", "Housel"),
    ("Misbehaving", "Thaler"),
    ("Nudge", "Thaler & Sunstein"),
    ("Predictably Irrational", "Ariely"),
    ("Your Money or Your Life", "Robin & Dominguez"),
    ("Die With Zero", "Perkins"),
    ("Atomic Habits (money-relevant chapters)", "Clear"),
    ("Stumbling on Happiness (money)", "Gilbert"),
    ("The Millionaire Next Door", "Stanley & Danko"),
    ("Happy Money", "Dunn & Norton"),
    ("Scarcity", "Mullainathan & Shafir"),
    ("Influence (money-relevant chapters)", "Cialdini"),
]


def utc_timestamp() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def call_gemini(client: genai.Client, prompt: str) -> str | None:
    try:
        response = client.models.generate_content(
            model=GEMINI_PRO,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.35,
                response_mime_type="application/json",
            ),
        )
        return response.text
    except Exception as exc:
        print(f"Gemini call failed: {exc}")
        return None


def parse_json_response(client: genai.Client, prompt: str) -> Any | None:
    for attempt in range(2):
        text = call_gemini(client, prompt)
        if text is None:
            return None
        try:
            return json.loads(strip_code_fences(text))
        except json.JSONDecodeError as exc:
            if attempt == 0:
                print(f"JSON parse failed, retrying once: {exc}")
                continue
            print(f"JSON parse failed after retry: {exc}")
    return None


def generation_prompt(
    book_title: str,
    author: str,
    names: list[str],
    count: int,
    timestamp: str,
) -> str:
    existing_names = ", ".join(sorted(names))
    return f"""
You are seeding a behavioral-finance concept corpus for a content pipeline.
The pipeline creates short-form videos explaining WHY people make bad money
decisions. Concepts must be psychological — about behavior, not advice.

Source book: "{book_title}" by {author}

Already in corpus (do NOT duplicate these):
{existing_names}

Generate {count} behavioral-finance concepts from this book. For each concept
return a JSON array. Each element must have EXACTLY these fields:
{{
  "concept_id":       slug, lowercase, underscores only,
  "canonical_name":   display name,
  "alternate_names":  list of 1-3 other names,
  "definition":       one sentence, 15-30 words, factual not advisory,
  "canonical_source": {{"author": "...", "work": "...", "year": YYYY, "url": null}},
  "scenarios": [
    {{"text": "relatable 1-2 sentence scenario", "tags": ["tag1","tag2"]}},
    {{"text": "...", "tags": [...]}},
    {{"text": "...", "tags": [...]}}
  ],
  "mechanism":        "1-2 sentences: why this happens psychologically",
  "visual_concepts":  list of 3-6 concrete visual search terms,
  "format_used":      [],
  "provenance":       "seeded",
  "provisional":      false,
  "quarantined":      false,
  "strikes":          0,
  "created_at":       "{timestamp}",
  "last_used_at":     null,
  "source_candidate_id": null
}}

Rules:
- Definition must describe a PSYCHOLOGICAL PATTERN, not give advice
- No forbidden phrases: "you should", "best way to", "guaranteed",
  "the rich", "passive income", "financial freedom"
- Scenarios must be relatable everyday situations, not abstract theory
- visual_concepts must be concrete searchable terms (e.g. "gym membership
  card", not "failure")
- Return ONLY the JSON array, no markdown, no explanation
""".strip()


def review_prompt(entries: list[ConceptEntry]) -> str:
    concepts_json = json.dumps([asdict(e) for e in entries], ensure_ascii=False)
    return f"""
Review this list of behavioral-finance concepts for a short-form content
pipeline. Flag any entry that has ANY of these problems:
  1. Definition gives advice rather than describing a psychological pattern
  2. Contains a forbidden phrase: "you should", "best way to", "guaranteed",
     "the rich", "passive income", "financial freedom"
  3. concept_id is not a valid slug (must match ^[a-z0-9_]+$)
  4. Scenarios are abstract theory rather than relatable everyday situations
  5. Duplicate of another entry in the same list (same concept, different name)

For each problem found return:
{{"concept_id": "...", "issue": "brief description"}}

Return a JSON array of problems. If no problems found, return [].
Input concepts: {concepts_json}
""".strip()


def ensure_not_overwriting() -> bool:
    if not CORPUS_PATH.exists():
        return True

    answer = input(f"Warning: {CORPUS_PATH} already exists. Overwrite? (y/n) ")
    return answer.strip().lower() == "y"


def init_model() -> genai.Client:
    try:
        from google.oauth2 import service_account as _sa
        project    = get_secret("GOOGLE_CLOUD_PROJECT")
        creds_path = get_secret("GOOGLE_APPLICATION_CREDENTIALS")
        credentials = _sa.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return genai.Client(vertexai=True, project=project, location="us-central1",
                            credentials=credentials)
    except Exception as exc:
        raise RuntimeError(f"Gemini client initialisation failed: {exc}") from exc


def entry_from_generated(raw_entry: dict[str, Any], timestamp: str) -> ConceptEntry | None:
    try:
        entry = dict(raw_entry)
        entry.setdefault("alternate_names", [])
        entry.setdefault("canonical_source", None)
        entry.setdefault("scenarios", [])
        entry.setdefault("mechanism", None)
        entry.setdefault("visual_concepts", [])
        entry.setdefault("format_used", [])
        entry.setdefault("provenance", "seeded")
        entry.setdefault("provisional", False)
        entry.setdefault("quarantined", False)
        entry.setdefault("strikes", 0)
        entry.setdefault("created_at", timestamp)
        entry.setdefault("last_used_at", None)
        entry.setdefault("source_candidate_id", None)
        return corpus_from_dict({
            "version": "1.0.0",
            "last_updated": timestamp,
            "concepts": {entry["concept_id"]: entry},
        }).concepts[entry["concept_id"]]
    except Exception as exc:
        concept_id = raw_entry.get("concept_id", "<missing>")
        print(f"Skipping malformed generated entry {concept_id}: {exc}")
        return None


def generate_entries(
    client: genai.Client,
    seed_corpus: ConceptCorpus,
) -> tuple[list[ConceptEntry], dict[str, str]]:
    generated_entries: list[ConceptEntry] = []
    source_books: dict[str, str] = {}
    collected_names = [
        entry.canonical_name for entry in seed_corpus.concepts.values()
    ]

    from tqdm import tqdm
    for index, (book_title, author) in tqdm(
        enumerate(SOURCE_BOOKS, start=1),
        total=len(SOURCE_BOOKS),
        desc="Seeding corpus",
        unit="book",
    ):
        requested_count = 8 if index == 1 else 7
        timestamp = utc_timestamp()
        prompt = generation_prompt(
            book_title=book_title,
            author=author,
            names=collected_names,
            count=requested_count,
            timestamp=timestamp,
        )
        parsed = parse_json_response(client, prompt)
        if not isinstance(parsed, list):
            tqdm.write(f"Book {index:2}/13: {book_title} → 0 concepts")
            continue

        batch: list[ConceptEntry] = []
        seen_ids = {entry.concept_id for entry in seed_corpus.concepts.values()}
        seen_ids.update(entry.concept_id for entry in generated_entries)
        seen_names = {name.lower() for name in collected_names}

        for raw_entry in parsed:
            if not isinstance(raw_entry, dict):
                continue
            entry = entry_from_generated(raw_entry, timestamp)
            if entry is None:
                continue
            if entry.concept_id in seen_ids:
                continue
            if entry.canonical_name.lower() in seen_names:
                continue
            batch.append(entry)
            seen_ids.add(entry.concept_id)
            seen_names.add(entry.canonical_name.lower())
            source_books[entry.concept_id] = book_title

        generated_entries.extend(batch)
        collected_names.extend(entry.canonical_name for entry in batch)
        tqdm.write(f"Book {index:2}/13: {book_title} → {len(batch)} concepts")

    return generated_entries, source_books


def review_entries(
    client: genai.Client,
    generated_entries: list[ConceptEntry],
) -> list[ConceptEntry]:
    print("Double-check pass...")
    parsed = parse_json_response(client, review_prompt(generated_entries))
    if not isinstance(parsed, list):
        return generated_entries

    remove_ids: set[str] = set()
    for issue in parsed:
        if not isinstance(issue, dict):
            continue
        concept_id = str(issue.get("concept_id", ""))
        issue_text = str(issue.get("issue", ""))
        print(f"⚠️  {concept_id}: {issue_text}")

        issue_lower = issue_text.lower()
        if (
            "advice" in issue_lower
            or "forbidden" in issue_lower
            or "duplicate" in issue_lower
            or "same concept" in issue_lower
        ):
            remove_ids.add(concept_id)

    return [
        entry for entry in generated_entries
        if entry.concept_id not in remove_ids
    ]


def validate_entries(
    entries: list[ConceptEntry],
    schema: dict[str, Any],
) -> list[ConceptEntry]:
    entry_schema = schema["definitions"]["ConceptEntry"]
    validator = Draft7Validator(entry_schema)
    passed: list[ConceptEntry] = []
    removed = 0

    for entry in entries:
        entry_dict = asdict(entry)
        errors = sorted(validator.iter_errors(entry_dict), key=lambda e: e.path)
        if errors:
            removed += 1
            for error in errors:
                path = ".".join(str(part) for part in error.path) or "<root>"
                print(f"Validation error {entry.concept_id}.{path}: {error.message}")
            continue
        passed.append(entry)

    print(f"Validation: {len(passed)} passed, {removed} removed")
    return passed


def write_corpus(
    seed_corpus: ConceptCorpus,
    generated_entries: list[ConceptEntry],
    source_books: dict[str, str],
) -> None:
    timestamp = utc_timestamp()
    concepts = dict(seed_corpus.concepts)
    concepts.update({entry.concept_id: entry for entry in generated_entries})
    corpus = ConceptCorpus(
        version="1.0.0",
        last_updated=timestamp,
        concepts=concepts,
    )

    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CORPUS_PATH.open("w", encoding="utf-8") as f:
        json.dump(asdict(corpus), f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"corpus/concepts.json written — {len(concepts)} total concepts")

    grouped: dict[str, list[ConceptEntry]] = {"Seed Examples": list(seed_corpus.concepts.values())}
    for entry in generated_entries:
        grouped.setdefault(source_books.get(entry.concept_id, "Unknown"), []).append(entry)

    with REVIEW_PATH.open("w", encoding="utf-8") as f:
        f.write(f"Money Psychology seed corpus review — {len(concepts)} total concepts\n\n")
        for source_book, entries in grouped.items():
            f.write(f"{source_book}\n")
            f.write("-" * len(source_book) + "\n")
            for entry in entries:
                f.write(
                    f"{entry.concept_id:<30} | "
                    f"{entry.canonical_name:<35} | "
                    f"{source_book}\n"
                )
            f.write("\n")
    print("corpus/seed_review.txt written")


def main() -> None:
    if not ensure_not_overwriting():
        print("Aborted.")
        return

    examples_data = load_json(EXAMPLES_PATH)
    seed_corpus = corpus_from_dict(examples_data)
    print(f"Loaded {len(seed_corpus.concepts)} seed entries.")

    schema = load_json(SCHEMA_PATH)
    client = init_model()
    generated_entries, source_books = generate_entries(client, seed_corpus)
    reviewed_entries = review_entries(client, generated_entries)
    validated_entries = validate_entries(reviewed_entries, schema)
    write_corpus(seed_corpus, validated_entries, source_books)


if __name__ == "__main__":
    main()
