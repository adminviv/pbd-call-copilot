"""The suggestion engine: transcript window -> topic -> papers -> 'ask next' cards.

Three Claude calls per window:
  1. read the latest stretch of conversation, name the topic, and pick search terms
  2. given only the retrieved papers, write up to 2 cards - or none
  3. claim check: each card's factual claims must be backed by its cited passage
     or by what was said on the call; unsupported cards are dropped

Claude may only cite papers it was handed; every citation is also checked
against NCBI (verify.py) and dropped if it doesn't resolve.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

import anthropic

import knowledge
import verify

MODEL = "claude-opus-5"

# Private settings live outside the code, so the app can be shared without them:
#   ~/.pbd.env                      ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID (one key for all PBD tools)
#   ~/.pbd-copilot/profile.md       who the CEO is, family context, PBD's topics of interest
SHARED_ENV = Path.home() / ".pbd.env"
DEFAULT_WORKSPACE = "wrkspc_01LfRGG3Ds73MxAPdchpbcgi"   # PBD workspace (an ID, not a secret)
PROFILE = Path(os.environ.get("COPILOT_PROFILE", Path.home() / ".pbd-copilot" / "profile.md"))


def _load_shared_env() -> None:
    """ANTHROPIC_API_KEY and ANTHROPIC_WORKSPACE_ID from ~/.pbd.env (env vars win)."""
    if not SHARED_ENV.exists():
        return
    for line in SHARED_ENV.read_text().splitlines():
        key, _, value = line.partition("=")
        if key in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID") and value.strip():
            os.environ.setdefault(key, value.strip())


_client = None


def get_client() -> "anthropic.Anthropic":
    """Created on first use, so the app can start (and ask for the key) before one exists."""
    global _client
    if _client is None:
        _load_shared_env()
        # The research@pbdproject.org key is not workspace-scoped, so every request names the workspace.
        _client = anthropic.Anthropic(
            default_headers={"anthropic-workspace-id": os.environ.get("ANTHROPIC_WORKSPACE_ID", DEFAULT_WORKSPACE)}
        )
    return _client


def has_key() -> bool:
    _load_shared_env()
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def save_key(key: str) -> str:
    """Check the key with a free call, then store it in ~/.pbd.env. Returns an error message or ''."""
    global _client
    if not key.startswith("sk-ant-"):
        return "That doesn't look like a PBD key. It starts with sk-ant-"
    try:
        anthropic.Anthropic(api_key=key, default_headers={
            "anthropic-workspace-id": os.environ.get("ANTHROPIC_WORKSPACE_ID", DEFAULT_WORKSPACE)}).models.list(limit=1)
    except anthropic.AuthenticationError:
        return "That key was not accepted. Check it was copied in full."
    except Exception as e:  # offline etc. - still save, the call itself will retry
        print("key check skipped:", e)
    lines = [l for l in (SHARED_ENV.read_text().splitlines() if SHARED_ENV.exists() else [])
             if not l.startswith("ANTHROPIC_API_KEY=")]
    SHARED_ENV.write_text("\n".join(lines + [f"ANTHROPIC_API_KEY={key}"]) + "\n")
    os.environ["ANTHROPIC_API_KEY"] = key
    _client = None
    return ""


def private_profile() -> str:
    return PROFILE.read_text() if PROFILE.exists() else ""


CONTEXT = """You support the CEO of PBD Project during live calls with researchers. On these calls he
speaks on behalf of PBD Project - he is not just asking science questions, he is representing the
organization.

PBD Project is a nonprofit research accelerator and biotech incubator for peroxisome science
("Incubating breakthroughs in peroxisome science"). Its three pillars:
- Cultivate ecosystems: bring researchers into PBD's network, connect labs that should work
  together, link patient communities with researchers.
- Unlock capital: fund labs directly, co-apply for or support grants (support letters), point
  researchers to funding.
- Incubate solutions: shared tools, assays, models and data, drug repurposing, turning findings into
  therapies.

He is NOT a scientist. Write everything he reads at a college-graduate level, not a scientist level:
everyday words, short sentences. When a technical term can't be avoided, explain it in the card's
'terms' list (e.g. "hexamer = a ring made of six copies of a protein").

What he wants most (his words: "The person said these things. What does that mean for us? What are
good clarifying questions to ask in topics we're interested in?"):
1. The implication for PBD of what the researcher just said, and a question to clarify it,
   pressure-test it, or find the next step (e.g. "have they tested this in an area we care about?").
2. A plain-language version of what the researcher just said, when they got technical - not a
   dictionary definition, but what they mean (e.g. "the PEX19 hexamer oxidizes X" -> "the protein
   PEX19 breaks down X").

The transcript comes from automatic transcription and garbles science terms (e.g. "series 48" =
CDC48, "PAX5" = PEX5, "P 97" = p97) and people's names. Always write names and terms in their
correct form.

Hard rules for anything he might say out loud:
- Never commit PBD Project's money, staff, strains, cell lines, data or other resources, and never
  claim PBD has something it may not have. Offer to explore instead ("Would a small pilot be useful?",
  "Would patient cell lines help?"). He decides what to commit.
- Never mention his family in the words he would say.
- Don't make him assert statistics or findings as fact unless the cited passage states them.

Private briefing (names, family context, PBD's topics of interest):
""" + private_profile()

# running cost tally (claude-opus-5: $5 / $25 per million input / output tokens)
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
PRICE_IN, PRICE_OUT = 5.0, 25.0


def cost_so_far() -> float:
    return (USAGE["input_tokens"] * PRICE_IN + USAGE["output_tokens"] * PRICE_OUT) / 1e6


def _call(system: str, user: str, schema: dict, effort: str) -> dict:
    resp = get_client().messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={
            "fallbacks": "default",
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        },
    )
    USAGE["calls"] += 1
    USAGE["input_tokens"] += resp.usage.input_tokens
    USAGE["output_tokens"] += resp.usage.output_tokens
    if resp.stop_reason == "refusal":
        return {}
    text = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


TOPIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["topic", "in_plain_words", "search_terms", "worth_suggesting"],
    "properties": {
        "topic": {"type": "string"},
        "in_plain_words": {"type": "string"},
        "search_terms": {"type": "array", "items": {"type": "string"}},
        "worth_suggesting": {"type": "boolean"},
    },
}

CARDS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cards"],
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "ask", "why", "terms", "pmcid", "connection"],
                "properties": {
                    "kind": {"type": "string", "enum": ["science", "next_step"]},
                    "ask": {"type": "string"},
                    "why": {"type": "string"},
                    "terms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["term", "plain"],
                            "properties": {"term": {"type": "string"}, "plain": {"type": "string"}},
                        },
                    },
                    "pmcid": {"type": "string"},
                    "connection": {"type": "string"},
                },
            },
        }
    },
}


def find_topic(window_text: str, recent_text: str) -> dict:
    return _call(
        CONTEXT,
        "Earlier in the call (for context only):\n" + recent_text[-3000:] +
        "\n\nLatest stretch of the call:\n" + window_text +
        "\n\nName the topic of the latest stretch in under 12 words (plain words). In 'in_plain_words', "
        "if the researcher said something technical, restate what they MEANT in 1-2 short sentences a "
        "college graduate would follow (their meaning, not definitions); leave it empty if nothing "
        "technical was said. Then give 3-6 "
        "short literature search terms (1-3 words each, e.g. 'Cdc48 Ubx2', 'pexophagy') using correct scientific terms (fix transcription errors; "
        "prefer distinctive terms like protein names over generic words). Set worth_suggesting "
        "to false only for small talk, greetings, or scheduling. Funding, collaboration and career-plan "
        "talk IS worth suggesting on - that's where PBD Project's next steps come from.",
        TOPIC_SCHEMA,
        effort="low",
    )


def write_cards(window_text: str, topic: str, papers: List[Dict], already_shown: List[str]) -> List[Dict]:
    sources = "\n\n".join(
        f"[{p['pmcid']}] {p['title']} ({p.get('source')})\n{p['text']}" for p in papers
    )
    return _call(
        CONTEXT,
        f"Current topic: {topic}\n\nLatest stretch of the call:\n{window_text}\n\n"
        f"Papers available (cite ONLY these, by the ID in brackets):\n{sources}\n\n"
        f"Questions already shown to Andrew: {json.dumps(already_shown)}\n"
        "Don't repeat any of them, and don't repeat the same idea or offer in new words. If the only "
        "good next step was already shown, return a science card or nothing.\n\n"
        "Write 0-2 suggestions for what Andrew, speaking for PBD Project, could say or ask next. "
        "Andrew glances at them mid-conversation, so each 'ask' is at most 30 words, in everyday words "
        "he can say out loud with confidence (college-graduate level, no scientist jargon), and specific "
        "to what the researcher just said. Prefer questions that clarify, pressure-test, or find the next "
        "step on a topic PBD cares about. Two kinds:\n"
        "- kind 'science': a question that clarifies or pressure-tests what the researcher said in light "
        "of PBD's topics. Must be backed by one of the papers above (set pmcid).\n"
        "- kind 'next_step': a concrete move for PBD Project, phrased as a proposal or question, never a "
        "commitment - float a pilot experiment, ask whether PBD resources (patient cell lines, models, "
        "PeroxiOS, a support letter, funding) would help, ask what the researcher would need, or suggest "
        "an introduction. Grounded in the call itself; set pmcid to "
        "an empty string unless a paper above directly supports it.\n"
        "'why' answers 'what does this mean for PBD?' in one plain sentence (at most 25 words): the "
        "implication of what the researcher said for the topics PBD cares about. "
        "'terms': every technical word left in 'ask' or 'why', each explained in at most 12 everyday "
        "words (an analogy is fine); empty list if there are none. "
        "'connection' names a person or lab worth connecting ONLY if that name appears verbatim in the "
        "call transcript or the paper text above; otherwise leave it empty. "
        "Never invent facts about PBD Project beyond the context given. If nothing genuinely useful "
        "fits, return no cards - silence beats a weak "
        "or generic suggestion.",
        CARDS_SCHEMA,
        effort="medium",
    ).get("cards", [])


CHECK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "supported", "problem"],
                "properties": {
                    "index": {"type": "integer"},
                    "supported": {"type": "boolean"},
                    "problem": {"type": "string"},
                },
            },
        }
    },
}


def check_claims(window_text: str, cards: List[Dict], by_id: Dict[str, Dict]) -> List[Dict]:
    """Second opinion on each card: are its factual claims backed, and does it follow the rules?"""
    if not cards:
        return []
    blocks = []
    for i, c in enumerate(cards):
        paper = by_id.get(c["pmcid"]) if c["pmcid"] else None
        passage = f"[{c['pmcid']}] {paper['title']}\n{paper['text']}" if paper else "(no paper cited)"
        blocks.append(f"CARD {i}\nkind: {c['kind']}\nask: {c['ask']}\nwhy: {c['why']}\n"
                      f"terms: {json.dumps(c.get('terms', []))}\n"
                      f"connection: {c['connection']}\ncited passage:\n{passage}")
    verdicts = _call(
        CONTEXT,
        f"Call transcript (latest stretch):\n{window_text}\n\n" + "\n\n".join(blocks) +
        "\n\nYou are the fact-checker. For each card, mark supported=false if ANY of these hold: a "
        "factual claim in 'ask' or 'why' is not stated in its cited passage and was not said on the call; "
        "the cited passage is about something else; the 'ask' commits PBD money or resources or claims "
        "PBD has something; the 'ask' mentions the CEO's family; a 'connection' names someone "
        "not in the transcript or passage; the 'ask' uses scientist jargon a college graduate wouldn't "
        "know without explaining it in 'terms'. General background any scientist would know is fine. Put the "
        "reason in 'problem' (empty string if supported).",
        CHECK_SCHEMA,
        effort="medium",
    ).get("verdicts", [])
    return verdicts


def suggest(window_text: str, recent_text: str, already_shown: List[str]) -> Dict:
    topic = find_topic(window_text, recent_text)
    result = {"topic": topic.get("topic"), "in_plain_words": topic.get("in_plain_words", ""), "search_terms": topic.get("search_terms", []), "cards": [], "dropped": []}
    if not topic.get("worth_suggesting"):
        return result
    papers = knowledge.search_all(topic.get("search_terms", []))
    result["papers_found"] = len(papers)
    by_id = {p["pmcid"]: p for p in papers}
    drafted = write_cards(window_text, topic["topic"], papers, already_shown)
    failed = {v["index"]: v["problem"] for v in check_claims(window_text, drafted, by_id) if not v["supported"]}
    for i, card in enumerate(drafted):
        if i in failed:
            result["dropped"].append({"card": card, "reason": "claim check: " + failed[i]})
            continue
        if not card["pmcid"]:
            if card["kind"] == "next_step":
                card["paper"] = None
                result["cards"].append(card)
            else:
                result["dropped"].append({"card": card, "reason": "science card without a paper"})
            continue
        paper = by_id.get(card["pmcid"])
        rec = verify.verify(card["pmcid"], paper["title"]) if paper else None
        if not rec:
            result["dropped"].append({"card": card, "reason": "citation did not verify"})
            continue
        card["paper"] = {"title": rec["title"], "journal": rec["journal"], "year": rec["year"],
                         "first_author": rec["first_author"], "pmid": rec["pmid"],
                         "found_in": paper.get("source")}
        result["cards"].append(card)
    return result
